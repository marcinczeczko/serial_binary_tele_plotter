"""
Background worker module for telemetry data processing.

This module handles the core logic of the application running in a separate thread.
It manages serial communication (reading/writing binary packets), parses incoming
data frames, buffers telemetry signals, and normalizes data for the GUI plotter.
It also supports a 'VIRTUAL' mode for testing without hardware.
"""

from enum import Enum, auto

import serial
from PyQt6 import QtCore

from core.data_manager import SignalDataManager
from core.protocol_handler import ProtocolHandler
from core.virtual_device import VirtualDevice

DEBUG_DECODE = False
TRACE_DECODE = False


class WorkerState(Enum):
    """
    Represents the operational lifecycle states of the TelemetryWorker.

    This state machine governs the internal logic to ensure data integrity
    and prevent invalid operations, such as attempting to acquire data
    before the signal mapping configuration is fully loaded.

    Attributes:
        IDLE: Initial state. No stream configuration or signal mappings are loaded.
        CONFIGURED: Signal definitions and frame decoders are applied. Ready to connect.
        RUNNING: Active data acquisition loop (Serial or Virtual) is executing.
    """

    IDLE = auto()
    CONFIGURED = auto()
    RUNNING = auto()


class TelemetryWorker(QtCore.QObject):
    """
    Worker class responsible for handling serial communication and data flow.

    It acts as a Controller that coordinates:
    - IO Sources: SerialPort or VirtualDevice
    - Processing: ProtocolHandler
    - Storage: SignalDataManager
    """

    data_ready = QtCore.pyqtSignal(dict)
    status_msg = QtCore.pyqtSignal(str)

    def __init__(self, sample_period_ms: float, max_samples: int):
        super().__init__()
        self.sample_period_s = sample_period_ms / 1000.0

        # --- Sub-components (Composition) ---
        self.data_mgr = SignalDataManager(max_samples)
        self.protocol = ProtocolHandler()
        self.virtual = VirtualDevice(parent=self)

        # Connect virtual device signal to data storage
        self.virtual.frame_generated.connect(self.data_mgr.store_frame)

        # --- IO & State ---
        self.serial_port = None
        self.state: WorkerState = WorkerState.IDLE

        # --- GUI Timer ---
        self.gui_update_timer = QtCore.QTimer(self)
        self.gui_update_timer.timeout.connect(self._emit_buffered_data)
        self.gui_update_timer.setInterval(33)  # ~30 FPS

    @QtCore.pyqtSlot(str, int)
    def start_working(self, port_name, baudrate):
        """Starts the data acquisition process."""
        if self.state != WorkerState.CONFIGURED:
            self.status_msg.emit("Worker not configured yet")
            return

        self.data_mgr.clear_all()
        self.state = WorkerState.RUNNING

        if port_name == "VIRTUAL":
            # Start virtual simulation
            self.virtual.start(self.sample_period_s, self.data_mgr.selected_motor)
        else:
            # Start serial connection
            try:
                self.serial_port = serial.Serial(port_name, baudrate, timeout=0.1)
                self.serial_port.reset_input_buffer()
                QtCore.QTimer.singleShot(0, self._serial_read_step)
            except (ValueError, serial.SerialException) as e:
                self.status_msg.emit(f"Connection Error: {e}")
                self.state = WorkerState.CONFIGURED
                return

        # Start GUI updates
        self.gui_update_timer.start()

    @QtCore.pyqtSlot()
    def stop_working(self):
        """Stops the worker safely."""
        self.gui_update_timer.stop()

        if self.state != WorkerState.RUNNING:
            return

        self.state = WorkerState.CONFIGURED

        # Stop sources
        self.virtual.stop()

        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.close()
            except (OSError, serial.SerialException):
                pass
            self.serial_port = None

    def _serial_read_step(self):
        """Reads raw bytes from serial and feeds the ProtocolHandler."""
        if self.state != WorkerState.RUNNING or not self.serial_port:
            return

        try:
            if not self.serial_port.is_open:
                raise serial.SerialException("Port closed unexpectedly")

            n = self.serial_port.in_waiting
            if n > 0:
                data = self.serial_port.read(n)
                if data:
                    self.protocol.add_data(data)
                    # Process all complete frames found in the buffer
                    for frame in self.protocol.process_available_frames():
                        self.data_mgr.store_frame(frame)

        except (serial.SerialException, OSError) as e:
            self.status_msg.emit(f"Serial error: {str(e)}")
            self.stop_working()
            return

        # Schedule next read
        QtCore.QTimer.singleShot(0, self._serial_read_step)

    def _emit_buffered_data(self):
        """Fetches processed data from manager and emits to GUI."""
        if self.state != WorkerState.RUNNING:
            return

        data = self.data_mgr.get_plot_data(self.sample_period_s)
        if data:
            self.data_ready.emit(data)

    @QtCore.pyqtSlot(float, int)
    def update_time_config(self, period_ms, max_samples):
        self.sample_period_s = period_ms / 1000.0
        self.data_mgr.update_max_samples(max_samples)

        # Update virtual device in real-time if active
        self.virtual.update_params(self.sample_period_s, self.data_mgr.selected_motor)

        # Update GUI timer interval if needed (optional logic)
        # self.gui_update_timer.setInterval(...)

    @QtCore.pyqtSlot(dict)
    def configure_signals(self, signals_cfg: dict):
        self.data_mgr.configure(signals_cfg)
        self.state = WorkerState.CONFIGURED

    def configure_frame(self, stream_cfg: dict):
        try:
            self.protocol.configure(stream_cfg)
        except ValueError as e:
            self.status_msg.emit(f"Frame Config Error: {e}")

    @QtCore.pyqtSlot(str, float, float)
    def update_scale(self, sig_id, ymin, ymax):
        self.data_mgr.update_scale(sig_id, ymin, ymax)

    @QtCore.pyqtSlot(int)
    def set_selected_motor(self, motor_id: int):
        self.data_mgr.set_motor(motor_id)
        # Also update virtual generator so it produces data for the right motor
        self.virtual.update_params(self.sample_period_s, motor_id)

    @QtCore.pyqtSlot(int, int, float, float, float, float, float)
    def send_pid_config(self, ramp_type, motor_id, kp, ki, kff, alpha, rps):
        if not self.serial_port or not self.serial_port.is_open:
            return

        packet = self.protocol.create_pid_packet(ramp_type, motor_id, kp, ki, kff, alpha, rps)
        try:
            self.serial_port.write(packet)
        except (serial.SerialTimeoutException, serial.SerialException) as e:
            print(f"Write Error: {e}")
