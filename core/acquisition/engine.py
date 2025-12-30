"""
Background engine module for telemetry data processing.

This module handles the core logic of the application running in a separate thread.
It acts as a Controller, orchestrating the flow of data between:
1. Input Sources (Serial Hardware or Virtual Simulator)
2. Protocol Logic (Parsing/Encoding)
3. Data Storage (Buffering/Normalization)
4. GUI Output (Signaling via Timer)
"""

import serial
from PyQt6 import QtCore

from core.acquisition.storage import SignalDataManager
from core.acquisition.virtual import VirtualDevice
from core.protocol.handler import ProtocolHandler
from core.types import EngineState


class TelemetryEngine(QtCore.QObject):
    """
    The main controller class for the background thread.
    """

    data_ready = QtCore.pyqtSignal(dict)
    status_msg = QtCore.pyqtSignal(str)

    def __init__(self, sample_period_ms: float, max_samples: int):
        super().__init__()
        self.sample_period_s = sample_period_ms / 1000.0

        # --- Sub-components (Composition Pattern) ---
        self.data_mgr = SignalDataManager(max_samples)
        self.protocol = ProtocolHandler()

        # 'parent=self' is crucial here! It ensures that when TelemetryEngine is moved
        # to a new QThread, the VirtualDevice (and its internal QTimer) moves with it.
        self.virtual = VirtualDevice(parent=self)
        self.virtual.frame_generated.connect(self.data_mgr.store_frame)

        # --- IO & State ---
        self.serial_port = None
        self.state: EngineState = EngineState.IDLE

        # --- GUI Update Timer ---
        self.gui_update_timer = QtCore.QTimer(self)
        self.gui_update_timer.timeout.connect(self._emit_buffered_data)
        self.gui_update_timer.setInterval(33)  # ~30 FPS

    @QtCore.pyqtSlot(str, int)
    def start_working(self, port_name, baudrate):
        """Initiates the data acquisition process."""
        if self.state != EngineState.CONFIGURED:
            self.status_msg.emit("Worker not configured yet")
            return

        # Clear buffers to prevent "time travel" artifacts
        self.data_mgr.clear_all()
        self.state = EngineState.RUNNING

        if port_name == "VIRTUAL":
            self.virtual.start(self.sample_period_s, self.data_mgr.selected_motor)
        else:
            try:
                self.serial_port = serial.Serial(port_name, baudrate, timeout=0.1)
                self.serial_port.reset_input_buffer()
                QtCore.QTimer.singleShot(0, self._serial_read_step)
            except (ValueError, serial.SerialException) as e:
                self.status_msg.emit(f"Connection Error: {e}")
                self.state = EngineState.CONFIGURED
                return

        self.gui_update_timer.start()

    @QtCore.pyqtSlot()
    def stop_working(self):
        """Safely stops all operations."""
        self.gui_update_timer.stop()

        if self.state != EngineState.RUNNING:
            return

        self.state = EngineState.CONFIGURED
        self.virtual.stop()

        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.close()
            except (OSError, serial.SerialException):
                pass
            self.serial_port = None

    @QtCore.pyqtSlot(int)
    def send_imu_command(self, cmd_id):
        # Tutaj logika wysyłania pakietu binarnego do MCU
        # self.protocol.create_imu_packet(cmd_id)...
        self.status_msg.emit(f"Sending IMU command: {cmd_id}")

    def _serial_read_step(self):
        """Performs a single, non-blocking read operation from the serial port."""
        if self.state != EngineState.RUNNING or not self.serial_port:
            return

        try:
            if not self.serial_port.is_open:
                raise serial.SerialException("Port closed unexpectedly")

            n = self.serial_port.in_waiting
            if n > 0:
                data = self.serial_port.read(n)
                if data:
                    self.protocol.add_data(data)
                    for frame in self.protocol.process_available_frames():
                        self.data_mgr.store_frame(frame)

        except (serial.SerialException, OSError) as e:
            self.status_msg.emit(f"Serial error: {str(e)}")
            self.stop_working()
            return

        QtCore.QTimer.singleShot(0, self._serial_read_step)

    def _emit_buffered_data(self):
        """Periodic task (triggered by gui_update_timer)."""
        if self.state != EngineState.RUNNING:
            return

        data = self.data_mgr.get_plot_data(self.sample_period_s)
        if data:
            self.data_ready.emit(data)

    @QtCore.pyqtSlot(float, int)
    def update_time_config(self, period_ms, max_samples):
        """Updates sampling settings and resizes buffers."""
        self.sample_period_s = period_ms / 1000.0
        self.data_mgr.update_max_samples(max_samples)
        self.virtual.update_params(self.sample_period_s, self.data_mgr.selected_motor)

    @QtCore.pyqtSlot(dict)
    def configure_signals(self, signals_cfg: dict):
        """Configures the Data Manager with the signal definitions."""
        self.data_mgr.configure(signals_cfg)
        self.state = EngineState.CONFIGURED

    # --- FIX: Dodano dekorator @QtCore.pyqtSlot(dict) ---
    # Jest to wymagane, aby metoda była widoczna dla QMetaObject.invokeMethod
    @QtCore.pyqtSlot(dict)
    def configure_frame(self, stream_cfg: dict):
        """Configures the Protocol Handler with the binary frame structure."""
        try:
            self.protocol.configure(stream_cfg)
            name = stream_cfg.get("name", "Unknown")
            self.virtual.configure_stream(name)
        except ValueError as e:
            self.status_msg.emit(f"Frame Config Error: {e}")

    @QtCore.pyqtSlot(str, float, float)
    def update_scale(self, sig_id, ymin, ymax):
        """Delegates scale updates to the Data Manager."""
        self.data_mgr.update_scale(sig_id, ymin, ymax)

    @QtCore.pyqtSlot(int)
    def set_selected_motor(self, motor_id: int):
        """Sets the active motor filter."""
        self.data_mgr.set_motor(motor_id)
        self.virtual.update_params(self.sample_period_s, motor_id)

    @QtCore.pyqtSlot(int, int, float, float, float, float, float)
    def send_pid_config(self, ramp_type, motor_id, kp, ki, kff, alpha, rps):
        """Constructs and sends a PID configuration packet to the MCU."""
        if not self.serial_port or not self.serial_port.is_open:
            return

        packet = self.protocol.create_pid_packet(ramp_type, motor_id, kp, ki, kff, alpha, rps)
        try:
            self.serial_port.write(packet)
        except (serial.SerialTimeoutException, serial.SerialException) as e:
            print(f"Write Error: {e}")
