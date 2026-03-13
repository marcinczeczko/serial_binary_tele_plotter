def test_configure_stream_types(pyqt_stub):
    from core.acquisition.virtual import VirtualDevice

    device = VirtualDevice()
    device.configure_stream("IMU 6-Axis")
    assert device._stream_type == "imu"
    device.configure_stream("Control Loop")
    assert device._stream_type == "control"
    device.configure_stream("PID Telemetry")
    assert device._stream_type == "pid"


def test_step_emits_frame(pyqt_stub):
    from core.acquisition.virtual import VirtualDevice

    device = VirtualDevice()
    captured = []

    def _capture(frame):
        captured.append(frame)

    device.frame_generated.connect(_capture)
    device.configure_stream("IMU")
    device._step()

    assert len(captured) == 1
    frame = captured[0]
    assert "acc_x" in frame
    assert "gyro_z" in frame
