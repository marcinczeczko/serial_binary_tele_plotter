import time
from unittest import mock

import serial

from tests.fakes import FakeTransport, wait_for


def test_configure_and_start_virtual(pyqt_stub):
    from core.acquisition.engine import TelemetryEngine

    engine = TelemetryEngine(sample_period_ms=10.0, max_samples=100)
    engine.configure_signals({"sig": {"field": "value"}})
    cfg = {
        "name": "IMU 6-Axis",
        "frame": {"stream_id": 1, "fields": []},
    }
    engine.configure_frame(cfg)
    assert engine.virtual._stream_type == "imu"

    with mock.patch.object(engine.virtual, "start") as vstart:
        engine.start_working("VIRTUAL", 115200)
        vstart.assert_called_once()
        assert engine.state.name == "RUNNING"


def test_serial_open_failure_emits_error(pyqt_stub):
    from core.acquisition.engine import TelemetryEngine

    engine = TelemetryEngine(sample_period_ms=10.0, max_samples=100)
    engine.configure_signals({"sig": {"field": "value"}})

    status_msgs = []
    fail_msgs = []
    engine.status_msg.connect(status_msgs.append)
    engine.connection_failed.connect(fail_msgs.append)

    with mock.patch("serial.Serial", side_effect=serial.SerialException("boom")):
        engine.start_working("COM_FAIL", 115200)

    assert engine.state.name == "CONFIGURED"
    assert status_msgs
    assert fail_msgs


def test_link_stats_report_counts_bytes_and_samples(pyqt_stub):
    from core.acquisition.engine import TelemetryEngine
    from core.protocol.crc import calculate_crc8

    engine = TelemetryEngine(sample_period_ms=10.0, max_samples=100)
    engine.configure_signals({"v": {"field": "v"}})
    engine.configure_frame(
        {
            "name": "t",
            "frame": {
                "stream_id": 1,
                "fields": [{"name": "loop_cntr", "type": "u8"}, {"name": "v", "type": "u8"}],
            },
        }
    )
    engine._stats_prev_ts = time.monotonic() - 1.0  # previous report 1 s ago

    header = bytes([0xAA, 0x55, 1, 2])
    for i in range(10):
        payload = bytes([i, 7])
        frame = (
            header + bytes([calculate_crc8(header)]) + payload + bytes([calculate_crc8(payload)])
        )
        engine.protocol.add_data(frame)
    engine.store.append(engine.protocol.process_available_frames())

    reports = []
    engine.link_stats.connect(reports.append)
    engine._emit_link_stats()

    assert len(reports) == 1
    report = reports[0]
    assert report["frames_decoded"] == 10
    assert report["bytes_rx"] == 80
    assert 0 < report["samples_per_s"] <= 10.0
    assert report["counter_missing"] == 0


_CFG_A = {"name": "A", "frame": {"stream_id": 1, "fields": []}, "signals": {"a": {"field": "a"}}}
_CFG_B = {"name": "B", "frame": {"stream_id": 2, "fields": []}, "signals": {"b": {"field": "b"}}}


def _running_virtual_engine():
    from core.acquisition.engine import TelemetryEngine

    engine = TelemetryEngine(sample_period_ms=10.0, max_samples=100)
    states = []
    engine.state_changed.connect(states.append)
    engine.select_stream(_CFG_A)
    engine.start_working("VIRTUAL", 115200)
    return engine, states


def _frames(n):
    from core.protocol.crc import calculate_crc8

    header = bytes([0xAA, 0x55, 1, 2])
    out = b""
    for i in range(n):
        payload = bytes([i % 256, 7])
        out += header + bytes([calculate_crc8(header)]) + payload + bytes([calculate_crc8(payload)])
    return out


_CFG_BYTES = {
    "name": "bytes",
    "frame": {
        "stream_id": 1,
        "fields": [{"name": "loop_cntr", "type": "u8"}, {"name": "v", "type": "u8"}],
    },
    "signals": {"v": {"field": "v"}},
}


def _engine_with(transport):
    from core.acquisition.engine import TelemetryEngine

    engine = TelemetryEngine(sample_period_ms=10.0, max_samples=500)
    engine.transport_factory = lambda port, baud: transport
    engine.select_stream(_CFG_BYTES)
    return engine


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
        assert wait_for(lambda: engine.store.total_stored == 200)
        assert engine.protocol.stats.bytes_rx == len(blob)
    finally:
        engine.stop_working()
    assert transport.closed
    assert engine._reader is None


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
    try:
        engine.send_left_config(1, 0, 0.1, 0.02, 1.0, 2.0, 3.0, 1.0, 0.2, -0.3)
        assert len(transport.written) == 1
        assert transport.written[0][:3] == bytes([0xAA, 0x55, 0x10])
    finally:
        engine.stop_working()


def test_write_failure_is_reported_not_fatal(pyqt_stub):
    transport = FakeTransport(fail_writes=True)
    engine = _engine_with(transport)
    msgs = []
    engine.status_msg.connect(msgs.append)
    engine.start_working("COM7", 115200)
    try:
        engine.send_left_config(1, 0, 0.1, 0.02, 1.0, 2.0, 3.0, 1.0, 0.2, 0.3)
        assert msgs[-1] == "Write Error: write timeout"
        assert engine.state.name == "RUNNING"
    finally:
        engine.stop_working()


def test_command_without_connection_is_reported(pyqt_stub):
    engine = _engine_with(FakeTransport())
    msgs = []
    engine.status_msg.connect(msgs.append)
    engine.send_left_config(1, 0, 0.1, 0.02, 1.0, 2.0, 3.0, 1.0, 0.2, 0.3)
    assert msgs == ["Not connected to a serial port: command not sent"]


def test_select_stream_while_running_restarts_on_same_port(pyqt_stub):
    engine, states = _running_virtual_engine()
    with mock.patch.object(engine.virtual, "start") as vstart:
        engine.select_stream(_CFG_B)

    vstart.assert_called_once()
    assert engine.state.name == "RUNNING"
    assert engine.store._fields == ["b"]
    assert engine.protocol.active_stream_id == 2
    assert [s.name for s in states] == ["CONFIGURED", "RUNNING", "CONFIGURED", "RUNNING"]


def test_select_stream_when_stopped_does_not_start(pyqt_stub):
    engine, _ = _running_virtual_engine()
    engine.stop_working()
    with mock.patch.object(engine.virtual, "start") as vstart:
        engine.select_stream(_CFG_B)
    vstart.assert_not_called()
    assert engine.state.name == "CONFIGURED"


def test_configure_signals_does_not_demote_running_engine(pyqt_stub):
    # C5 regression: this used to force CONFIGURED and silently stall acquisition.
    engine, _ = _running_virtual_engine()
    engine.configure_signals(_CFG_B["signals"])
    assert engine.state.name == "RUNNING"


def test_start_when_running_reports_already_running(pyqt_stub):
    engine, _ = _running_virtual_engine()
    msgs = []
    engine.status_msg.connect(msgs.append)
    engine.start_working("VIRTUAL", 115200)
    assert msgs == ["Already running"]
