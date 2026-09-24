"""FrameParser + RecordDecoder + StreamRouter: the engine's multi-stream decode path."""

from __future__ import annotations

import random
import struct

import numpy as np
import pytest

from core.protocol.crc import calculate_crc8
from core.protocol.frame_parser import FrameParser
from core.protocol.handler import ProtocolHandler
from core.protocol.record_decoder import RecordDecoder, frame_dtype
from core.protocol.router import StreamRouter
from core.types import StreamConfig, StreamFrameField

FIELDS: list[StreamFrameField] = [
    {"name": "loop_cntr", "type": "u32"},
    {"name": "a", "type": "f32"},
    {"name": "b", "type": "i16"},
    {"name": "c", "type": "u8"},
    {"name": "d", "type": "f64"},
    {"name": "e", "type": "i64"},
]
FMT = "IfhBdq"


def _frame(stream_id: int, payload: bytes) -> bytes:
    header = bytes([0xAA, 0x55, stream_id, len(payload)])
    return header + bytes([calculate_crc8(header)]) + payload + bytes([calculate_crc8(payload)])


def _stream(stream_id: int, fields: list[StreamFrameField], endian: str = "little") -> StreamConfig:
    return {
        "name": f"s{stream_id}",
        "frame": {"stream_id": stream_id, "endianness": endian, "fields": fields},
        "signals": {},
    }


@pytest.mark.parametrize(("endian", "prefix"), [("little", "<"), ("big", ">")])
def test_record_decoder_matches_struct(endian: str, prefix: str) -> None:
    rng = random.Random(1)
    values = [
        (
            i,
            rng.uniform(-5, 5),
            rng.randint(-30000, 30000),
            rng.randint(0, 255),
            rng.random(),
            -i,
        )
        for i in range(50)
    ]
    payloads = [struct.pack(prefix + FMT, *v) for v in values]
    decoder = RecordDecoder(endian, FIELDS)
    assert decoder.size == struct.calcsize(prefix + FMT)

    records = decoder.decode_many(payloads)

    for rec, payload in zip(records, payloads, strict=True):
        expected = struct.unpack(prefix + FMT, payload)
        assert tuple(rec.tolist()) == pytest.approx(expected)


def test_frame_dtype_rejects_unknown_types() -> None:
    with pytest.raises(ValueError, match="u128"):
        frame_dtype("little", [{"name": "x", "type": "u128"}])


def test_vectorised_path_equals_per_frame_decoder_on_a_noisy_stream() -> None:
    """The router must decode exactly what the old dict-per-frame handler decodes."""
    rng = random.Random(7)
    cfg = _stream(4, FIELDS)
    parts: list[bytes] = []
    for i in range(3000):
        payload = struct.pack("<" + FMT, i, i * 0.5, i % 30000, i % 100, i / 3, -i)
        frame = bytearray(_frame(4, payload))
        roll = rng.random()
        if roll < 0.02:
            frame[-1] ^= 0xFF  # payload CRC error
        elif roll < 0.04:
            frame[4] ^= 0xFF  # header CRC error
        parts.append(bytes(frame))
        if rng.random() < 0.05:
            parts.append(bytes(rng.randrange(256) for _ in range(rng.randint(1, 20))))
    blob = b"".join(parts)
    chunks = []
    pos = 0
    while pos < len(blob):
        n = rng.randint(1, 3000)
        chunks.append(blob[pos : pos + n])
        pos += n

    handler = ProtocolHandler()
    handler.configure(cfg)
    expected: list[dict[str, int | float]] = []
    for chunk in chunks:
        handler.add_data(chunk)
        expected.extend(handler.process_available_frames())

    parser = FrameParser()
    router = StreamRouter(parser.stats)
    router.configure({"s": cfg})
    got = [router.route(parser.feed(chunk)).get("s") for chunk in chunks]
    records = np.concatenate([r for r in got if r is not None])

    assert len(records) == len(expected) > 2500
    for rec, exp in zip(records, expected, strict=True):
        assert rec["loop_cntr"] == exp["loop_cntr"]
        assert rec["d"] == pytest.approx(exp["d"])
        assert rec["e"] == exp["e"]
    for counter in (
        "frames_decoded",
        "header_crc_errors",
        "payload_crc_errors",
        "counter_gaps",
        "counter_missing",
        "counter_resets",
    ):
        assert getattr(parser.stats, counter) == getattr(handler.stats, counter), counter


def test_shared_id_routes_by_payload_size_and_shares_identical_layouts() -> None:
    small: list[StreamFrameField] = [
        {"name": "loop_cntr", "type": "u8"},
        {"name": "x", "type": "u8"},
    ]
    large: list[StreamFrameField] = [
        {"name": "loop_cntr", "type": "u8"},
        {"name": "y", "type": "f32"},
    ]
    router = StreamRouter()
    router.configure(
        {
            "small": _stream(1, small),
            "small_view": _stream(1, small),
            "large": _stream(1, large),
            "other": _stream(2, small),
        }
    )
    frames = [
        (1, bytes([0, 9])),
        (1, bytes([0]) + struct.pack("<f", 2.5)),
        (1, bytes([1, 8])),
        (1, b"\x00\x00\x00"),  # 3 bytes: no layout of that size for ID 1
        (7, b"\x00"),  # unknown ID
    ]

    out = router.route(frames)

    assert list(out["small"]["x"]) == [9, 8]
    assert out["small_view"] is out["small"]  # decoded once
    assert list(out["large"]["y"]) == [2.5]
    assert "other" not in out
    assert router.stats.size_mismatches == 1
    assert router.stats.unknown_id_frames == 1
    assert router.stats.frames_decoded == 3


def test_ambiguous_same_size_layouts_decode_with_the_first_definition() -> None:
    first: list[StreamFrameField] = [
        {"name": "loop_cntr", "type": "u8"},
        {"name": "x", "type": "i8"},
    ]
    second: list[StreamFrameField] = [
        {"name": "loop_cntr", "type": "u8"},
        {"name": "y", "type": "u8"},
    ]
    router = StreamRouter()
    router.configure({"first": _stream(1, first), "second": _stream(1, second)})
    out = router.route([(1, bytes([0, 255]))])
    assert list(out) == ["first"]
    assert out["first"]["x"][0] == -1


def test_counter_tracking_across_batches() -> None:
    fields: list[StreamFrameField] = [{"name": "loop_cntr", "type": "u32"}]
    router = StreamRouter()
    router.configure({"s": _stream(1, fields)})

    def batch(*counters: int) -> list[tuple[int, bytes]]:
        return [(1, struct.pack("<I", c)) for c in counters]

    router.route(batch(10, 11, 12))
    router.route(batch(15, 16))  # gap of 2 across the batch boundary
    router.route(batch(0, 1))  # reset
    router.route(batch(1))  # repeat counts as a reset too
    stats = router.stats
    assert (stats.counter_gaps, stats.counter_missing, stats.counter_resets) == (1, 2, 2)
    router.reset_counters()
    router.route(batch(100))
    assert stats.counter_gaps == 1
