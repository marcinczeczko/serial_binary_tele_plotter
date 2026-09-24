import struct
import time
from unittest import mock

import serial

from tests.fakes import FakeTransport, wait_for


def _frame(stream_id, payload):
    from core.protocol.crc import calculate_crc8

    header = bytes([0xAA, 0x55, stream_id, len(payload)])
    return header + bytes([calculate_crc8(header)]) + payload + bytes([calculate_crc8(payload)])


def _frames(n, stream_id=1):
    """n frames of stream A (u8 loop_cntr, u8 v)."""
    return b"".join(_frame(stream_id, bytes([i % 256, 7])) for i in range(n))


_CFG_BYTES = {
    "name": "bytes",
    "frame": {
        "stream_id": 1,
        "fields": [{"name": "loop_cntr", "type": "u8"}, {"name": "v", "type": "u8"}],
    },
    "signals": {"v": {"field": "v"}},
}
_CFG_B = {
    "name": "B",
    "frame": {
        "stream_id": 2,
        "fields": [{"name": "loop_cntr", "type": "u8"}, {"name": "w", "type": "i16"}],
    },
    "signals": {"w": {"field": "w"}},
}
# Same ID and layout as A, different signal selection: decoded once, fed to both.
_CFG_A_VIEW = {**_CFG_BYTES, "name": "A view", "signals": {"counter": {"field": "loop_cntr"}}}
_CFG_IMU = {
    "name": "IMU sim",
    "frame": {
        "stream_id": 3,
        "fields": [{"name": "loop_cntr", "type": "u32"}, {"name": "acc_x", "type": "f32"}],
    },
    "signals": {"ax": {"field": "acc_x"}},
}
STREAMS = {"a": _CFG_BYTES, "b": _CFG_B, "a_view": _CFG_A_VIEW, "imu": _CFG_IMU}


def _engine(transport=None):
    from core.acquisition.engine import TelemetryEngine

    engine = TelemetryEngine(max_samples=500)
    if transport is not None:
        engine.transport_factory = lambda port, baud: transport
    return engine


def _engine_with(transport):
    engine = _engine(transport)
    engine.configure_streams(STREAMS)
    engine.select_stream("a")
    return engine


def test_configure_streams_builds_a_store_per_stream(pyqt_stub):
    engine = _engine()
    states, configured = [], []
    engine.state_changed.connect(states.append)
    engine.streams_configured.connect(lambda: configured.append(True))

    engine.configure_streams(STREAMS)

    assert [s.name for s in states] == ["CONFIGURED"]
    assert configured == [True]
    assert sorted(engine.stores.keys()) == sorted(STREAMS)


def test_start_before_configuring_is_refused(pyqt_stub):
    engine = _engine()
    msgs = []
    engine.status_msg.connect(msgs.append)
    engine.start_working("VIRTUAL", 115200)
    assert msgs == ["No stream configured yet"]
    assert engine.state.name == "IDLE"


def test_virtual_port_streams_simulated_bytes_through_the_parser(pyqt_stub):
    engine = _engine()
    engine.configure_streams(STREAMS)
    engine.select_stream("imu")
    msgs = []
    engine.status_msg.connect(msgs.append)

    engine.start_working("VIRTUAL", 115200)
    try:
        assert engine.state.name == "RUNNING"
        assert msgs[-1] == "Connected to VIRTUAL"
        imu = engine.stores.get("imu")
        assert wait_for(lambda: len(imu) >= 5)
        assert len(engine.stores.get("a")) == 0  # only the shown stream is simulated
        stats = engine.parser.stats
        assert stats.bytes_rx > 0 and stats.frames_decoded >= 5  # real frames, parsed
        assert stats.errors == 0 and stats.counter_gaps == 0

        engine.select_stream("b")  # retargets the simulator; no restart
        b = engine.stores.get("b")
        assert wait_for(lambda: len(b) >= 5)
        assert engine.state.name == "RUNNING"
    finally:
        engine.stop_working()
    assert engine._sim is None and engine._reader is None


def test_virtual_port_needs_a_valid_stream(pyqt_stub):
    engine = _engine()
    engine.configure_streams({})  # e.g. every stream in the file has errors
    msgs = []
    engine.status_msg.connect(msgs.append)
    engine.start_working("VIRTUAL", 115200)
    assert msgs == ["No valid stream to simulate"]
    assert engine.state.name == "CONFIGURED"


def test_commands_on_the_virtual_port_reach_the_simulator(pyqt_stub):
    engine = _engine()
    engine.configure_streams({"pid": {**_CFG_B, "sim": {"model": "pid_motor"}}})
    msgs = []
    engine.status_msg.connect(msgs.append)
    # Any config-defined command works (R5.2): here one carrying only three left gains.
    from core.protocol.commands import CommandDef, CommandField, encode_command

    tune = CommandDef(
        "tune",
        "Left tune",
        0x20,
        (
            CommandField("left_kp", "f32"),
            CommandField("left_rps", "f32"),
            CommandField("left_use_pi", "u8"),
        ),
    )
    engine.configure_commands((tune,))  # before connecting: the simulator gets them on start
    engine.start_working("VIRTUAL", 115200)
    try:
        sim = engine._sim
        engine.send_packet(
            encode_command(tune, {"left_kp": 1.5, "left_rps": 2.0, "left_use_pi": 0})
        )
        gains = sim.synth.model.gains("left")
        assert (gains.kp, gains.rps, gains.use_pi) == (1.5, 2.0, False)
        assert sim.synth.model.gains("right").kp == 0.1  # untouched
        assert not any("not sent" in m for m in msgs)
    finally:
        engine.stop_working()
    engine.send_packet(encode_command(tune, {"left_kp": 1, "left_rps": 1, "left_use_pi": 1}))
    assert msgs[-1] == "Not connected to a serial port: command not sent"


def test_serial_open_failure_emits_error(pyqt_stub):
    engine = _engine()
    engine.configure_streams(STREAMS)
    status_msgs, fail_msgs = [], []
    engine.status_msg.connect(status_msgs.append)
    engine.connection_failed.connect(fail_msgs.append)

    with mock.patch("serial.Serial", side_effect=serial.SerialException("boom")):
        engine.start_working("COM_FAIL", 115200)

    assert engine.state.name == "CONFIGURED"
    assert status_msgs and fail_msgs


def test_serial_data_is_read_on_reader_thread_and_stored(pyqt_stub):
    blob = _frames(200)
    chunks = [blob[i : i + 97] for i in range(0, len(blob), 97)]  # frames split across reads
    transport = FakeTransport(chunks)
    engine = _engine_with(transport)
    msgs = []
    engine.status_msg.connect(msgs.append)

    engine.start_working("COM7", 115200)
    try:
        assert engine.state.name == "RUNNING"
        assert msgs[-1] == "Connected to COM7"
        store = engine.stores.get("a")
        assert wait_for(lambda: store.total_stored == 200)
        snap = store.snapshot()
        assert list(snap.signals["v"][:3]) == [7, 7, 7]
        assert engine.parser.stats.bytes_rx == len(blob)
    finally:
        engine.stop_working()
    assert transport.closed
    assert engine._reader is None


def test_every_configured_stream_is_decoded_at_once(pyqt_stub):
    parts = []
    for i in range(30):
        parts.append(_frame(1, bytes([i, 5])))
        parts.append(_frame(2, bytes([i]) + struct.pack("<h", -i)))
        parts.append(_frame(9, b"\x00\x01\x02"))  # nobody decodes ID 9
    transport = FakeTransport([b"".join(parts)])
    engine = _engine_with(transport)

    engine.start_working("COM7", 115200)
    try:
        assert wait_for(lambda: engine.stores.get("b").total_stored == 30)
        a = engine.stores.get("a").snapshot()
        view = engine.stores.get("a_view").snapshot()
        b = engine.stores.get("b").snapshot()
        assert list(a.signals["v"]) == [5] * 30
        assert list(view.signals["counter"]) == list(range(30))  # same frames, other signals
        assert list(b.signals["w"]) == [-i for i in range(30)]
        stats = engine.parser.stats
        assert stats.unknown_id_frames == 30
        assert stats.frames_decoded == 60  # A and "A view" share one decode
        assert stats.counter_gaps == 0
    finally:
        engine.stop_working()


def test_select_stream_while_running_does_not_restart(pyqt_stub):
    transport = FakeTransport()
    engine = _engine_with(transport)
    engine.start_working("COM7", 115200)
    reader = engine._reader
    states = []
    engine.state_changed.connect(states.append)
    try:
        engine.select_stream("b")
        assert engine.state.name == "RUNNING"
        assert engine._reader is reader  # same session, history kept
        assert states == []
    finally:
        engine.stop_working()


def test_configure_streams_while_running_keeps_running(pyqt_stub):
    # C5 analogue: reconfiguring must never demote RUNNING and stall acquisition.
    transport = FakeTransport()
    engine = _engine_with(transport)
    engine.start_working("COM7", 115200)
    try:
        engine.configure_streams({"b": _CFG_B})
        assert engine.state.name == "RUNNING"
        assert engine.stores.keys() == ["b"]
    finally:
        engine.stop_working()


def test_start_when_running_reports_already_running(pyqt_stub):
    engine = _engine_with(FakeTransport())
    engine.start_working("COM7", 115200)
    msgs = []
    engine.status_msg.connect(msgs.append)
    try:
        engine.start_working("COM7", 115200)
        assert msgs == ["Already running"]
    finally:
        engine.stop_working()


def test_link_stats_report_counts_bytes_and_samples(pyqt_stub):
    engine = _engine_with(FakeTransport())
    engine._stats_prev_ts = time.monotonic() - 1.0  # previous report 1 s ago
    engine._on_bytes(_frames(10))

    reports = []
    engine.link_stats.connect(reports.append)
    engine._emit_link_stats()

    (report,) = reports
    assert report["frames_decoded"] == 10
    assert report["bytes_rx"] == 80
    assert 0 < report["samples_per_s"] <= 25.0  # 10 in "a" + 10 in "a_view", over ~1 s
    assert report["counter_missing"] == 0


def test_transport_failure_stops_engine_and_reports(pyqt_stub):
    transport = FakeTransport([_frames(5)], fail_when_drained=True)
    engine = _engine_with(transport)
    failures = []
    engine.connection_failed.connect(failures.append)

    engine.start_working("COM7", 115200)

    assert wait_for(lambda: engine.state.name == "CONFIGURED")
    assert failures == ["Serial error: device disconnected"]
    assert transport.closed


def test_commands_are_written_to_the_transport(pyqt_stub):
    transport = FakeTransport()
    engine = _engine_with(transport)
    engine.start_working("COM7", 115200)
    packet = _frame(0x10, bytes(35))
    try:
        engine.send_packet(packet)
        assert transport.written == [packet]  # written as encoded, in one piece
    finally:
        engine.stop_working()


def test_write_failure_is_reported_not_fatal(pyqt_stub):
    transport = FakeTransport(fail_writes=True)
    engine = _engine_with(transport)
    msgs = []
    engine.status_msg.connect(msgs.append)
    engine.start_working("COM7", 115200)
    try:
        engine.send_packet(_frame(0x10, bytes(35)))
        assert msgs[-1] == "Write Error: write timeout"
        assert engine.state.name == "RUNNING"
    finally:
        engine.stop_working()


def test_command_without_connection_is_reported(pyqt_stub):
    engine = _engine_with(FakeTransport())
    msgs = []
    engine.status_msg.connect(msgs.append)
    engine.send_packet(_frame(0x10, bytes(35)))
    assert msgs == ["Not connected to a serial port: command not sent"]


def test_counter_reset_is_reported_and_time_keeps_growing(pyqt_stub):
    engine = _engine_with(FakeTransport())
    msgs = []
    engine.status_msg.connect(msgs.append)
    engine._on_bytes(b"".join(_frame(1, bytes([i, 7])) for i in (10, 11, 12, 0, 1)))

    engine._emit_link_stats()
    engine._emit_link_stats()  # reported once, not on every stats tick

    assert msgs == [
        "Time counter went backwards in bytes, A view (device reset?); continuing on a new segment"
    ]
    snap = engine.stores.get("a").snapshot()
    assert (snap.time[1:] > snap.time[:-1]).all()


def test_set_time_scale_retimes_one_stream(pyqt_stub):
    engine = _engine_with(FakeTransport())
    engine._on_bytes(_frames(3))
    engine.set_time_scale("a", 0.5)
    assert engine.stores.get("a").snapshot().time.tolist() == [0.0, 0.5, 1.0]
    assert engine.stores.get("a_view").time_scale_s == 0.005  # the other view keeps its own
    engine.set_time_scale("missing", 1.0)  # ignored


def test_set_capacity_resizes_every_store(pyqt_stub):
    engine = _engine_with(FakeTransport())
    engine._on_bytes(_frames(20))
    engine.set_capacity(5)
    assert len(engine.stores.get("a")) == 5 and len(engine.stores.get("b")) == 0
    assert engine.stores.get("a").capacity == 5
