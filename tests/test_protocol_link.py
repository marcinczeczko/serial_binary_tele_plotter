"""The decoder slot (R8.1): the binary decoder behind `LinkDecoder` behaves as before."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from core.protocol.frame_parser import FrameParser
from core.protocol.link import BinaryFrameDecoder, LinkDecoder, make_link_decoder
from core.protocol.router import StreamRouter
from core.simulation.synth import FrameSynth
from core.types import StreamConfig

REPO_ROOT = Path(__file__).resolve().parent.parent


def _streams() -> dict[str, StreamConfig]:
    doc = json.loads((REPO_ROOT / "streams.json").read_text(encoding="utf-8"))
    streams: dict[str, StreamConfig] = doc["streams"]
    return streams


def test_the_binary_decoder_matches_parser_plus_router() -> None:
    streams = _streams()
    blob = FrameSynth(streams["pid"], seed=1).frames(0, 500) + FrameSynth(
        streams["imu_6axis"], seed=2
    ).frames(0, 300)
    link = make_link_decoder("binary")
    link.configure(streams)
    parser = FrameParser()
    router = StreamRouter(parser.stats)
    router.configure(streams)

    got: dict[str, list[np.ndarray]] = {}
    want: dict[str, list[np.ndarray]] = {}
    for off in range(0, len(blob), 777):
        chunk = blob[off : off + 777]
        for key, records in link.feed(chunk).items():
            got.setdefault(key, []).append(records)
        for key, records in router.route(parser.feed(chunk)).items():
            want.setdefault(key, []).append(records)

    assert set(got) == set(want) == {"pid", "pid_ff", "imu_6axis"}
    for key in want:
        assert np.array_equal(np.concatenate(got[key]), np.concatenate(want[key]))
    assert link.stats == parser.stats
    assert link.stats.frames_decoded == 800


def test_reset_starts_a_new_connection() -> None:
    streams = _streams()
    link = make_link_decoder()
    link.configure(streams)
    frames = FrameSynth(streams["imu_6axis"], seed=3).frames(0, 10)
    link.feed(frames[:-3])  # the last frame is left incomplete in the buffer
    before = link.stats

    link.reset()

    assert link.stats is not before and link.stats.bytes_rx == 0
    assert link.feed(frames[-3:]) == {}  # the old partial frame was dropped
    assert isinstance(link, BinaryFrameDecoder)
    assert link.router.stats is link.stats  # the router counts into the new statistics


def test_the_decoder_counts_what_it_cant_decode() -> None:
    link: LinkDecoder = make_link_decoder()
    link.configure({})
    streams = _streams()
    link.feed(FrameSynth(streams["imu_6axis"], seed=4).frames(0, 5) + b"garbage")
    assert link.stats.unknown_id_frames == 5
    assert link.stats.discarded_bytes == len(b"garbage")


def test_an_unknown_format_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown link format 'morse'"):
        make_link_decoder("morse")
