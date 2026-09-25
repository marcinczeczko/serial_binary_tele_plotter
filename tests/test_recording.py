"""Raw recordings (R4.1) and their replay through the pipeline (R4.2)."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import pytest

from core.recording.sbtp import (
    RecordingError,
    RecordingReader,
    RecordingWriter,
    recording_name,
)
from core.transport import TransportError
from core.transport.replay_transport import MAX_READ_BYTES, ReplayEnded, ReplayTransport
from tests.fakes import FakeTransport, wait_for
from tests.test_acquisition_engine import STREAMS, _engine, _frame, _frames

FIXTURE = Path(__file__).parent / "fixtures" / "pid_sim_300.sbtp"
STREAM_CFG = {"a": {"frame": {"stream_id": 1, "fields": []}}}


def _write(path: Path, chunks: list[tuple[int, bytes]]) -> None:
    w = RecordingWriter(path, STREAM_CFG, "COM7")
    for ts, data in chunks:
        w.write(data, ts_ns=ts)
    w.close()


def test_round_trip_keeps_header_chunks_and_timestamps(tmp_path: Path) -> None:
    path = tmp_path / "r.sbtp"
    _write(path, [(1_000, b"abc"), (2_500, b"\x00\xff"), (2_600, b"")])  # empty is skipped

    reader = RecordingReader(path)

    assert reader.header.source == "COM7"
    assert reader.header.streams == STREAM_CFG
    assert reader.header.version == 1 and reader.header.created
    assert list(reader.chunks()) == [(1_000, b"abc"), (2_500, b"\x00\xff")]
    assert reader.truncated_bytes == 0


def test_recordings_are_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "r.sbtp"
    _write(path, [])
    with pytest.raises(FileExistsError):
        RecordingWriter(path, STREAM_CFG, "COM7")


def test_a_cut_off_last_chunk_is_tolerated_and_counted(tmp_path: Path) -> None:
    path = tmp_path / "r.sbtp"
    _write(path, [(1, b"first"), (2, b"second-chunk")])
    path.write_bytes(path.read_bytes()[:-5])  # killed mid-write

    reader = RecordingReader(path)
    assert list(reader.chunks()) == [(1, b"first")]
    assert reader.truncated_bytes == 12 + len(b"second-chunk") - 5


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "too short"),
        (b"NOPE" + b"\x00" * 6, "bad magic"),
        (struct.pack("<4sHI", b"SBTP", 99, 2) + b"{}", "unsupported recording version 99"),
        (struct.pack("<4sHI", b"SBTP", 1, 2) + b"{}", "no stream configuration"),
    ],
)
def test_invalid_files_are_rejected(tmp_path: Path, content: bytes, message: str) -> None:
    path = tmp_path / "bad.sbtp"
    path.write_bytes(content)
    with pytest.raises(RecordingError, match=message):
        RecordingReader(path)


def test_recording_names_are_safe() -> None:
    from datetime import datetime

    name = recording_name("/dev/ttyUSB0 pid", datetime(2026, 9, 24, 18, 15, 2))
    assert name == "_dev_ttyUSB0_pid_20260924-181502.sbtp"


# --- ReplayTransport pacing (fake clock) ---


class _Clock:
    def __init__(self) -> None:
        self.now = 50.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _replay(tmp_path: Path, speed: float | None = 1.0) -> tuple[ReplayTransport, _Clock]:
    path = tmp_path / "r.sbtp"
    if not path.exists():
        _write(path, [(0, b"a"), (100_000_000, b"b"), (300_000_000, b"c")])
    clock = _Clock()
    replay = ReplayTransport(path, speed, clock=clock, sleep=clock.sleep)
    replay.open()
    return replay, clock


def test_replay_follows_recorded_timing(tmp_path: Path) -> None:
    replay, clock = _replay(tmp_path)
    assert replay.read(0.05) == b"a"  # due at once
    assert replay.read(0.05) == b""  # b is due 100 ms after a
    clock.now += 0.06
    assert replay.read(0.05) == b"b"
    clock.now += 0.2
    assert replay.read(0.05) == b"c"
    assert replay.finished and replay.progress == 1.0
    with pytest.raises(ReplayEnded):
        replay.read(0.05)


def test_replay_speed_and_max_speed(tmp_path: Path) -> None:
    replay, clock = _replay(tmp_path, speed=2.0)
    replay.read(0.01)
    clock.now += 0.051  # 100 ms of recording at 2x
    assert replay.read(0.001) == b"b"
    replay.set_speed(None)  # as fast as possible: the rest at once
    assert replay.read(0.001) == b"c"

    fast, _ = _replay(tmp_path, speed=None)
    assert fast.read(0.001) == b"abc"  # batched up to MAX_READ_BYTES
    assert MAX_READ_BYTES >= 3


def test_replay_pause_and_step(tmp_path: Path) -> None:
    replay, clock = _replay(tmp_path)
    replay.set_paused(True)
    clock.now += 10.0
    assert replay.read(0.01) == b""  # paused: nothing, however late
    replay.step()
    assert replay.read(0.01) == b"a"
    assert replay.read(0.01) == b""
    replay.set_paused(False)  # resumes from "a", so "b" is 100 ms away, not overdue
    assert replay.read(0.05) == b""
    clock.now += 0.06
    assert replay.read(0.05) == b"b"


def test_replay_has_no_device_to_command(tmp_path: Path) -> None:
    replay, _ = _replay(tmp_path)
    with pytest.raises(TransportError):
        replay.write(b"\x01")
    replay.close()
    with pytest.raises(TransportError):
        replay.read(0.01)


# --- engine: record a session, replay it ---


def _wait_idle(engine: Any) -> bool:
    return wait_for(lambda: engine.state.name == "CONFIGURED", timeout_s=5.0)


def test_record_then_replay_reproduces_the_session(pyqt_stub: Any, tmp_path: Path) -> None:
    parts = [_frames(40), b"\x13\x37garbage", _frame(2, bytes([0]) + struct.pack("<h", -5))]
    transport = FakeTransport()
    live = _engine(transport)
    live.configure_streams(STREAMS)
    changes: list[str] = []
    live.recording_changed.connect(changes.append)
    live.start_working("COM7", 115200)
    path = tmp_path / "session.sbtp"
    live.start_recording(str(path))
    transport.push(*parts)
    try:
        assert wait_for(lambda: live.link.stats.bytes_rx == sum(map(len, parts)))
    finally:
        live.stop_working()  # also stops the recording
    assert changes == [str(path), ""]

    replayed = _engine()
    replayed.configure_streams(STREAMS)
    ended: list[str] = []
    replayed.session_ended.connect(ended.append)
    replayed.start_replay(str(path), 0.0)  # as fast as possible
    # The engine stops first, then announces the end: wait for the announcement.
    assert wait_for(lambda: bool(ended), timeout_s=5.0)

    assert ended == ["Replay finished: session.sbtp"]
    assert replayed.state.name == "CONFIGURED"
    live_stats, replay_stats = live.link.stats, replayed.link.stats
    assert replay_stats == live_stats  # same bytes, same framing, same errors
    for key in ("a", "a_view", "b"):
        assert replayed.stores.get(key).total_stored == live.stores.get(key).total_stored
    a, b = live.stores.get("a").snapshot(), replayed.stores.get("a").snapshot()
    assert a is not None and b is not None
    assert a.time.tolist() == b.time.tolist()
    assert a.signals["v"].tolist() == b.signals["v"].tolist()


def test_recording_needs_a_session(pyqt_stub: Any, tmp_path: Path) -> None:
    engine = _engine()
    engine.configure_streams(STREAMS)
    msgs: list[str] = []
    engine.status_msg.connect(msgs.append)
    engine.start_recording(str(tmp_path / "x.sbtp"))
    assert msgs == ["Connect first: a recording starts with a session"]
    assert not (tmp_path / "x.sbtp").exists()


def test_bad_replay_file_is_reported(pyqt_stub: Any, tmp_path: Path) -> None:
    engine = _engine()
    engine.configure_streams(STREAMS)
    failures: list[str] = []
    engine.connection_failed.connect(failures.append)
    (tmp_path / "junk.sbtp").write_bytes(b"junk")
    engine.start_replay(str(tmp_path / "junk.sbtp"), 1.0)
    assert failures and failures[0].startswith("Cannot replay:")
    assert engine.state.name == "CONFIGURED"


def test_committed_fixture_replays_to_known_statistics(pyqt_stub: Any) -> None:
    """
    `fixtures/pid_sim_300.sbtp`: 300 simulated frames of the bundled `pid` stream (seed 7),
    recorded as 30 reads 10 ms apart, with one payload byte of frame 125 flipped. It pins the
    file format (old recordings must keep replaying) and the whole receive path.
    """
    header = ReplayTransport(FIXTURE).header
    engine = _engine()
    engine.configure_streams(header.streams)  # decode with the config it was recorded with
    engine.start_replay(str(FIXTURE), 0.0)
    assert _wait_idle(engine)

    stats = engine.link.stats
    assert stats.frames_decoded == 299
    assert stats.payload_crc_errors == 1 and stats.header_crc_errors == 0
    assert stats.counter_gaps == 1 and stats.counter_missing == 1
    assert stats.discarded_bytes == 0
    store = engine.stores.get("pid")
    assert store.total_stored == 299 and store.time_gaps == 1


# --- profiles (R8.2) ---


def test_old_recordings_are_binary_and_new_ones_name_their_profile(tmp_path: Path) -> None:
    assert ReplayTransport(FIXTURE).header.profile_format == "binary"  # made before profiles
    path = tmp_path / "p.sbtp"
    RecordingWriter(path, STREAM_CFG, "COM7", profile={"name": "esc-2", "format": "x"}).close()
    header = RecordingReader(path).header
    assert header.extra["profile"] == {"name": "esc-2", "format": "x"}
    assert header.profile_format == "x"


def test_a_replay_decodes_with_the_recorded_format(
    pyqt_stub: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    from core.protocol import link as link_module

    class OtherFormat(link_module.BinaryFrameDecoder):
        pass

    monkeypatch.setitem(link_module.LINK_FORMATS, "other", OtherFormat)
    transport = FakeTransport()
    live = _engine(transport)
    live.configure_streams(STREAMS)
    live.configure_profile("gadget", "other")
    assert isinstance(live.link, OtherFormat)
    live.start_working("COM7", 115200)
    path = tmp_path / "other.sbtp"
    live.start_recording(str(path))
    transport.push(_frames(10))
    try:
        assert wait_for(lambda: live.link.stats.frames_decoded == 10)
    finally:
        live.stop_working()
    assert RecordingReader(path).header.extra["profile"] == {"name": "gadget", "format": "other"}

    replayed = _engine()  # a binary profile
    replayed.configure_streams(STREAMS)
    msgs: list[str] = []
    replayed.status_msg.connect(msgs.append)
    replayed.start_replay(str(path), 0.0)
    assert isinstance(replayed.link, OtherFormat)  # the recording's format, not the profile's
    assert _wait_idle(replayed)
    assert replayed.link.stats.frames_decoded == 10
    assert any("recorded as other, not this profile's binary" in m for m in msgs)

    replayed.transport_factory = lambda _port, _baud: FakeTransport()
    replayed.start_working("COM7", 115200)  # a live session: the profile's format again
    try:
        assert type(replayed.link) is link_module.BinaryFrameDecoder
    finally:
        replayed.stop_working()


def test_an_unknown_format_refuses_to_connect(pyqt_stub: Any) -> None:
    engine = _engine()
    engine.configure_streams(STREAMS)
    failures: list[str] = []
    engine.connection_failed.connect(failures.append)
    engine.configure_profile("broken", "morse")
    engine.start_working("COM7", 115200)
    assert failures == ["Cannot decode: unknown link format 'morse' (known: binary, text)"]
    assert engine.state.name == "CONFIGURED"


# --- text profiles (R8.3) ---


def _text_streams() -> dict[str, Any]:
    import json

    fixture = Path(__file__).parent / "fixtures" / "text_profile.json"
    streams: dict[str, Any] = json.loads(fixture.read_text(encoding="utf-8"))["streams"]
    return streams


def test_a_text_profile_plots_from_virtual_and_replays_the_same(
    pyqt_stub: Any, tmp_path: Path
) -> None:
    from core.protocol.text_line import TextLineDecoder

    streams = _text_streams()
    live = _engine()
    live.configure_profile("arduino-imu", "text")
    live.configure_streams(streams)
    live.select_stream("imu")
    assert isinstance(live.link, TextLineDecoder)
    live.start_working("VIRTUAL", 115200)
    path = tmp_path / "text.sbtp"
    live.start_recording(str(path))
    try:
        assert wait_for(lambda: live.stores.get("imu").total_stored >= 150, timeout_s=5.0)
    finally:
        live.stop_working()
    stats = live.link.stats
    assert stats.lines_unmatched >= 1  # the simulator's "# sim tick"
    assert stats.counter_gaps == stats.value_errors == stats.lines_overlong == 0
    assert RecordingReader(path).header.extra["profile"]["format"] == "text"

    replayed = _engine()
    replayed.configure_profile("arduino-imu", "text")
    replayed.configure_streams(streams)
    replayed.start_replay(str(path), 0.0)
    assert _wait_idle(replayed)
    assert replayed.link.stats == stats
    a, b = live.stores.get("imu").snapshot(), replayed.stores.get("imu").snapshot()
    assert a is not None and b is not None and len(a.time) >= 150
    assert a.time.tolist() == b.time.tolist()
    assert a.signals["ax"].tolist() == b.signals["ax"].tolist()
