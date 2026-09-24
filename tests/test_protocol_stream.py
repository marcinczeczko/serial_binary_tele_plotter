"""
Stream-level tests for ProtocolHandler: chunking, noise, corruption and multiplexing.

These feed realistic byte streams through add_data()/process_available_frames() the way the
engine does, instead of single well-formed frames. Randomised cases use fixed seeds so every
run is deterministic.
"""

from __future__ import annotations

import random
import struct
from collections.abc import Iterable, Iterator

import pytest

from core.protocol.constants import MAGIC_0, MAGIC_1
from core.protocol.crc import calculate_crc8
from core.protocol.handler import ProtocolHandler
from core.types import StreamConfig, StreamFrameField

STREAM_ID = 7
OTHER_STREAM_ID = 9

STREAM: StreamConfig = {
    "name": "test",
    "frame": {
        "stream_id": STREAM_ID,
        "endianness": "little",
        "fields": [
            {"name": "loop_cntr", "type": "u32"},
            {"name": "a", "type": "f32"},
            {"name": "b", "type": "i16"},
        ],
    },
    "signals": {},
}
PAYLOAD_FMT = "<Ifh"


def build_frame(stream_id: int, payload: bytes) -> bytes:
    header = bytes([MAGIC_0, MAGIC_1, stream_id, len(payload)])
    return header + bytes([calculate_crc8(header)]) + payload + bytes([calculate_crc8(payload)])


def data_frame(i: int) -> bytes:
    return build_frame(STREAM_ID, struct.pack(PAYLOAD_FMT, i, i * 0.5, (i % 200) - 100))


def expected(i: int) -> dict[str, int | float]:
    return {"loop_cntr": i, "a": i * 0.5, "b": (i % 200) - 100}


def make_handler(cfg: StreamConfig = STREAM) -> ProtocolHandler:
    handler = ProtocolHandler()
    handler.configure(cfg)
    return handler


def feed(
    handler: ProtocolHandler, data: bytes, sizes: Iterable[int]
) -> list[dict[str, int | float]]:
    """Feeds `data` in chunks of the given sizes (cycled) and collects every decoded frame."""
    out: list[dict[str, int | float]] = []
    pos = 0
    size_iter = iter(sizes)
    while pos < len(data):
        n = next(size_iter)
        handler.add_data(data[pos : pos + n])
        pos += n
        out.extend(handler.process_available_frames())
    return out


def random_sizes(seed: int, lo: int, hi: int) -> Iterator[int]:
    rng = random.Random(seed)
    while True:
        yield rng.randint(lo, hi)


N_FRAMES = 2000
STREAM_BYTES = b"".join(data_frame(i) for i in range(N_FRAMES))
ALL_EXPECTED = [expected(i) for i in range(N_FRAMES)]


@pytest.mark.parametrize("seed", range(10))
def test_random_small_chunks_decode_every_frame(seed: int) -> None:
    assert feed(make_handler(), STREAM_BYTES, random_sizes(seed, 1, 1024)) == ALL_EXPECTED


def test_byte_by_byte_decodes_every_frame() -> None:
    assert feed(make_handler(), STREAM_BYTES, iter(lambda: 1, 0)) == ALL_EXPECTED


@pytest.mark.xfail(
    strict=True,
    reason="C1: reads larger than 4 KiB are discarded unparsed (fixed by roadmap R1.1)",
)
@pytest.mark.parametrize("seed", range(3))
def test_large_chunks_decode_every_frame(seed: int) -> None:
    assert feed(make_handler(), STREAM_BYTES, random_sizes(seed, 4097, 16384)) == ALL_EXPECTED


@pytest.mark.parametrize("seed", range(5))
def test_garbage_between_frames_is_skipped(seed: int) -> None:
    rng = random.Random(seed)
    no_magic = [b for b in range(256) if b != MAGIC_0]
    parts: list[bytes] = []
    for i in range(N_FRAMES):
        parts.append(bytes(rng.choice(no_magic) for _ in range(rng.randint(0, 40))))
        parts.append(data_frame(i))
    data = b"".join(parts)
    assert feed(make_handler(), data, random_sizes(seed, 1, 1024)) == ALL_EXPECTED


def test_fake_magic_with_bad_header_crc_is_skipped() -> None:
    fake_header = bytes([MAGIC_0, MAGIC_1, STREAM_ID, 10])
    bad_crc = (calculate_crc8(fake_header) + 1) & 0xFF
    fake = fake_header + bytes([bad_crc])
    data = b"".join(fake + data_frame(i) for i in range(100))
    assert feed(make_handler(), data, random_sizes(1, 1, 64)) == ALL_EXPECTED[:100]


def test_corrupted_header_crc_drops_only_that_frame() -> None:
    frames = [bytearray(data_frame(i)) for i in range(100)]
    frames[42][4] ^= 0xFF
    data = b"".join(frames)
    decoded = feed(make_handler(), data, random_sizes(2, 1, 256))
    assert decoded == [e for i, e in enumerate(ALL_EXPECTED[:100]) if i != 42]


def test_corrupted_payload_crc_drops_only_that_frame() -> None:
    frames = [bytearray(data_frame(i)) for i in range(100)]
    frames[17][-1] ^= 0xFF
    frames[18][7] ^= 0x01  # flipped payload bit, CRC byte untouched
    data = b"".join(frames)
    decoded = feed(make_handler(), data, random_sizes(3, 1, 256))
    assert decoded == [e for i, e in enumerate(ALL_EXPECTED[:100]) if i not in (17, 18)]


def test_frame_split_between_magic_bytes() -> None:
    handler = make_handler()
    frame = data_frame(5)
    handler.add_data(data_frame(4) + frame[:1])  # ends with 0xAA
    assert list(handler.process_available_frames()) == [expected(4)]
    handler.add_data(frame[1:])
    assert list(handler.process_available_frames()) == [expected(5)]


def test_wrong_payload_length_for_stream_is_dropped() -> None:
    short = build_frame(STREAM_ID, struct.pack("<If", 1, 1.0))  # missing the i16 field
    data = data_frame(0) + short + data_frame(1)
    assert feed(make_handler(), data, [len(data)]) == [expected(0), expected(1)]


def test_max_length_payload() -> None:
    fields: list[StreamFrameField] = [{"name": "loop_cntr", "type": "u32"}]
    fields += [{"name": f"u{k}", "type": "u8"} for k in range(251)]
    cfg: StreamConfig = {
        "name": "max",
        "frame": {"stream_id": 3, "endianness": "little", "fields": fields},
        "signals": {},
    }
    payload = struct.pack("<I", 123) + bytes(range(251))
    assert len(payload) == 255
    data = build_frame(3, payload) * 3
    decoded = feed(make_handler(cfg), data, random_sizes(4, 1, 300))
    assert len(decoded) == 3
    assert decoded[0]["loop_cntr"] == 123
    assert decoded[0]["u250"] == 250


def test_other_stream_ids_are_skipped_without_losing_sync() -> None:
    rng = random.Random(5)
    parts: list[bytes] = []
    for i in range(500):
        if rng.random() < 0.5:
            other_len = rng.randint(0, 60)
            parts.append(
                build_frame(OTHER_STREAM_ID, bytes(rng.randrange(256) for _ in range(other_len)))
            )
        parts.append(data_frame(i))
    data = b"".join(parts)
    assert feed(make_handler(), data, random_sizes(6, 1, 512)) == ALL_EXPECTED[:500]


def test_big_endian_stream() -> None:
    cfg: StreamConfig = {
        "name": "be",
        "frame": {
            "stream_id": STREAM_ID,
            "endianness": "big",
            "fields": STREAM["frame"]["fields"],
        },
        "signals": {},
    }
    frame = build_frame(STREAM_ID, struct.pack(">Ifh", 9, 4.5, -7))
    assert feed(make_handler(cfg), frame, [len(frame)]) == [{"loop_cntr": 9, "a": 4.5, "b": -7}]
