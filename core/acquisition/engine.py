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

DEBUG_DECODE = False
TRACE_DECODE = False


class TelemetryEngine(QtCore.QObject):
    """
    The main controller class for the background thread.

    It coordinates the interaction between the Serial Port (or Virtual Device),
    the Protocol Handler (parsing), and the Data Manager (storage).

    It is designed to be moved to a QThread to prevent blocking the main GUI.

    Attributes:
        data_ready (pyqtSignal): Emitted periodically by the GUI timer containing
                                 processed data for plotting.
        status_msg (pyqtSignal): Emitted to report errors or status updates to the UI.
    """

    data_ready = QtCore.pyqtSignal(dict)
    status_msg = QtCore.pyqtSignal(str)

    def __init__(self, sample_period_ms: float, max_samples: int):
        """
        Initializes the Engine and its sub-components.

        Args:
            sample_period_ms (float): Sampling period in milliseconds (e.g., 5.0).
            max_samples (int): Size of the ring buffer for data storage.
        """
        super().__init__()
        self.sample_period_s = sample_period_ms / 1000.0

        # --- Sub-components (Composition Pattern) ---
        self.data_mgr = SignalDataManager(max_samples)
        self.protocol = ProtocolHandler()

        # 'parent=self' is crucial here! It ensures that when TelemetryEngine is moved
        # to a new QThread, the VirtualDevice (and its internal QTimer) moves with it.
        self.virtual = VirtualDevice(parent=self)

        # Direct wiring: Virtual device generates a dict -> Data Manager stores it.
        self.virtual.frame_generated.connect(self.data_mgr.store_frame)

        # --- IO & State ---
        self.serial_port = None
        self.state: EngineState = EngineState.IDLE

        # --- GUI Update Timer ---
        # This timer decouples the high-speed data acquisition (e.g., 1kHz)
        # from the visual rendering (e.g., 30Hz). This prevents UI freezing.
        self.gui_update_timer = QtCore.QTimer(self)
        self.gui_update_timer.timeout.connect(self._emit_buffered_data)
        self.gui_update_timer.setInterval(33)  # ~30 FPS

    @QtCore.pyqtSlot(str, int)
    def start_working(self, port_name, baudrate):
        """
        Initiates the data acquisition process.

        Depending on the port_name, it starts either the Serial connection
        or the Virtual simulation.

        Args:
            port_name (str): 'VIRTUAL' or a COM port (e.g., 'COM3').
            baudrate (int): Serial communication speed.
        """
        if self.state != EngineState.CONFIGURED:
            self.status_msg.emit("Worker not configured yet")
            return

        # Clear buffers to prevent "time travel" artifacts on the plot
        # (connecting to a new session with old data in RAM).
        self.data_mgr.clear_all()
        self.state = EngineState.RUNNING

        if port_name == "VIRTUAL":
            # Start virtual simulation
            self.virtual.start(self.sample_period_s, self.data_mgr.selected_motor)
        else:
            # Start hardware connection
            try:
                self.serial_port = serial.Serial(port_name, baudrate, timeout=0.1)
                self.serial_port.reset_input_buffer()

                # Schedule the first read step immediately.
                # We use QTimer.singleShot(0) to enter the recursive read loop.
                QtCore.QTimer.singleShot(0, self._serial_read_step)
            except (ValueError, serial.SerialException) as e:
                self.status_msg.emit(f"Connection Error: {e}")
                self.state = EngineState.CONFIGURED
                return

        # Start the timer that feeds data to the GUI
        self.gui_update_timer.start()

    @QtCore.pyqtSlot()
    def stop_working(self):
        """
        Safely stops all operations.

        1. Stops the GUI update timer.
        2. Stops data sources (Virtual or Serial).
        3. Closes resources.
        """
        # Stop GUI updates first to prevent accessing closed resources
        self.gui_update_timer.stop()

        if self.state != EngineState.RUNNING:
            return

        self.state = EngineState.CONFIGURED

        # Stop sources
        self.virtual.stop()

        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.close()
            except (OSError, serial.SerialException):
                pass
            self.serial_port = None

    def _serial_read_step(self):
        """
        Performs a single, non-blocking read operation from the serial port.

        This method is designed to be called recursively via QTimer.singleShot(0).
        This allows the Qt Event Loop to process other events (signals, slot calls)
        in between read operations, keeping the thread responsive.
        """
        if self.state != EngineState.RUNNING or not self.serial_port:
            return

        try:
            # Check logical connection state
            if not self.serial_port.is_open:
                raise serial.SerialException("Port closed unexpectedly")

            n = self.serial_port.in_waiting
            if n > 0:
                data = self.serial_port.read(n)
                if data:
                    # 1. Push raw bytes into the protocol handler
                    self.protocol.add_data(data)

                    # 2. Extract and store any complete frames found
                    for frame in self.protocol.process_available_frames():
                        self.data_mgr.store_frame(frame)

        except (serial.SerialException, OSError) as e:
            # Handle physical disconnection (e.g., cable pulled out)
            self.status_msg.emit(f"Serial error: {str(e)}")
            self.stop_working()
            return

        # Schedule the next read immediately, yielding to the event loop first
        QtCore.QTimer.singleShot(0, self._serial_read_step)

    def _emit_buffered_data(self):
        """
        Periodic task (triggered by gui_update_timer).
        Fetches normalized plotting data from the Manager and emits it to the GUI.
        """
        if self.state != EngineState.RUNNING:
            return

        data = self.data_mgr.get_plot_data(self.sample_period_s)
        if data:
            self.data_ready.emit(data)

    @QtCore.pyqtSlot(float, int)
    def update_time_config(self, period_ms, max_samples):
        """
        Updates sampling settings and resizes buffers.
        """
        self.sample_period_s = period_ms / 1000.0
        self.data_mgr.update_max_samples(max_samples)

        # Update virtual device in real-time if active
        self.virtual.update_params(self.sample_period_s, self.data_mgr.selected_motor)

    @QtCore.pyqtSlot(dict)
    def configure_signals(self, signals_cfg: dict):
        """
        Configures the Data Manager with the signal definitions (mappings, ranges).
        """
        self.data_mgr.configure(signals_cfg)
        self.state = EngineState.CONFIGURED

    def configure_frame(self, stream_cfg: dict):
        """
        Configures the Protocol Handler with the binary frame structure.
        """
        try:
            self.protocol.configure(stream_cfg)
        except ValueError as e:
            self.status_msg.emit(f"Frame Config Error: {e}")

    @QtCore.pyqtSlot(str, float, float)
    def update_scale(self, sig_id, ymin, ymax):
        """Delegates scale updates to the Data Manager."""
        self.data_mgr.update_scale(sig_id, ymin, ymax)

    @QtCore.pyqtSlot(int)
    def set_selected_motor(self, motor_id: int):
        """
        Sets the active motor filter.
        Updates both the Data Manager (view) and Virtual Device (source).
        """
        self.data_mgr.set_motor(motor_id)
        self.virtual.update_params(self.sample_period_s, motor_id)

    @QtCore.pyqtSlot(int, int, float, float, float, float, float)
    def send_pid_config(self, ramp_type, motor_id, kp, ki, kff, alpha, rps):
        """
        Constructs and sends a PID configuration packet to the MCU.

        Args:
            ramp_type (int): 0 for Step, 1 for Ramp.
            motor_id (int): Motor index (0 or 1).
            kp (float): Proportional term.
            ki (float): Integral term.
            kff (float): Feed-forward term.
            alpha (float): Low-pass filter coefficient.
            rps (float): Target velocity.
        """
        if not self.serial_port or not self.serial_port.is_open:
            return

        packet = self.protocol.create_pid_packet(ramp_type, motor_id, kp, ki, kff, alpha, rps)
        try:
            self.serial_port.write(packet)
        except (serial.SerialTimeoutException, serial.SerialException) as e:
            print(f"Write Error: {e}")
