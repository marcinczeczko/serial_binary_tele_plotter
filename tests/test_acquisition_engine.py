import time
from unittest import mock

import serial


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
    for decoded in engine.protocol.process_available_frames():
        engine.data_mgr.store_frame(decoded)

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


def test_serial_open_uses_write_timeout(pyqt_stub):
    from core.acquisition.engine import SERIAL_WRITE_TIMEOUT_S, TelemetryEngine

    engine = TelemetryEngine(sample_period_ms=10.0, max_samples=100)
    engine.select_stream(_CFG_A)
    msgs = []
    engine.status_msg.connect(msgs.append)
    port = mock.MagicMock(in_waiting=0, is_open=True)
    with mock.patch("serial.Serial", return_value=port) as serial_cls:
        engine.start_working("COM7", 230400)

    serial_cls.assert_called_once_with(
        "COM7", 230400, timeout=0.1, write_timeout=SERIAL_WRITE_TIMEOUT_S
    )
    assert engine.state.name == "RUNNING"
    assert msgs[-1] == "Connected to COM7"


def test_select_stream_while_running_restarts_on_same_port(pyqt_stub):
    engine, states = _running_virtual_engine()
    with mock.patch.object(engine.virtual, "start") as vstart:
        engine.select_stream(_CFG_B)

    vstart.assert_called_once()
    assert engine.state.name == "RUNNING"
    assert engine.data_mgr._field_map == {"b": "b"}
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
