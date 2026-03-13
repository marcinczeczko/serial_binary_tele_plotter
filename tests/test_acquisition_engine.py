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
