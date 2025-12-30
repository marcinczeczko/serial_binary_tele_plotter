"""
Background worker module for telemetry data processing.

This module handles the core logic of the application running in a separate thread.
It manages serial communication (reading/writing binary packets), parses incoming
data frames, buffers telemetry signals, and normalizes data for the GUI plotter.
It also supports a 'VIRTUAL' mode for testing without hardware.
"""

import math
import random
import struct
from enum import Enum, auto

import serial
from PyQt6 import QtCore

from core.data_manager import SignalDataManager
from core.frame_decoder import FrameDecoder
from core.protocol import MAGIC_0, MAGIC_1, RTP_REQ_PID, calculate_crc8

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

    It delegates data storage and processing to SignalDataManager to keep
    responsibilities separated. It runs in a separate QThread.

    Attributes:
        data_ready (pyqtSignal): Emitted when a new batch of data is processed.
        status_msg (pyqtSignal): Emitted to report errors or status updates.
    """

    data_ready = QtCore.pyqtSignal(dict)
    status_msg = QtCore.pyqtSignal(str)

    def __init__(self, sample_period_ms: float, max_samples: int):
        super().__init__()
        self.sample_period_s = sample_period_ms / 1000.0

        # Delegate data management to helper class
        self.data_mgr = SignalDataManager(max_samples)

        self.rx_buffer = bytearray()
        self.frame_decoder = None
        self.active_stream_id = None
        self._virtual_loop_cntr = 0

        self.timer = None
        self.serial_port = None
        self.state: WorkerState = WorkerState.IDLE

        # GUI Update Timer (Decoupled from Serial Loop)
        self.gui_update_timer = QtCore.QTimer(self)
        self.gui_update_timer.timeout.connect(self._emit_buffered_data)
        self.gui_update_timer.setInterval(33)  # ~30 FPS

    @QtCore.pyqtSlot(str, int)
    def start_working(self, port_name, baudrate):
        """
        Starts the data acquisition process.
        """
        if self.state != WorkerState.CONFIGURED:
            self.status_msg.emit("Worker not configured yet")
            if TRACE_DECODE:
                print(f"[STATE] start_working blocked, state={self.state}")
            return

        # Clear buffers to ensure the plot starts fresh
        self.data_mgr.clear_all()

        self.state = WorkerState.RUNNING
        if TRACE_DECODE:
            print("[STATE] RUNNING")

        if port_name == "VIRTUAL":
            self._virtual_loop_cntr = 0
            if self.timer is None:
                self.timer = QtCore.QTimer(self)
                self.timer.timeout.connect(self._step)
            self.timer.start(int(self.sample_period_s * 1000))
        else:
            try:
                self.serial_port = serial.Serial(port_name, baudrate, timeout=0.1)
                self.serial_port.reset_input_buffer()
                QtCore.QTimer.singleShot(0, self._serial_read_step)
            except serial.SerialException as e:
                self.status_msg.emit(f"Connection Error: {e}")
                self.state = WorkerState.CONFIGURED
                return
            except ValueError as e:
                self.status_msg.emit(f"Config Error: {e}")
                self.state = WorkerState.CONFIGURED
                return

        self.gui_update_timer.start()

    @QtCore.pyqtSlot()
    def stop_working(self):
        """
        Stops the worker safely.
        """
        self.gui_update_timer.stop()

        if self.state != WorkerState.RUNNING:
            return

        if TRACE_DECODE:
            print("[STATE] STOP -> CONFIGURED")

        self.state = WorkerState.CONFIGURED

        if self.timer:
            self.timer.stop()

        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.close()
            except (OSError, serial.SerialException):
                pass
            self.serial_port = None

    def _serial_read_step(self):
        """
        Performs a single non-blocking read cycle from the serial port.
        """
        if self.state != WorkerState.RUNNING:
            return

        if self.serial_port is None:
            return

        try:
            if not self.serial_port.is_open:
                raise serial.SerialException("Port closed unexpectedly")

            n = self.serial_port.in_waiting
            if n > 0:
                data = self.serial_port.read(n)
                if data:
                    if TRACE_DECODE:
                        print(f"[RX][READ] got {len(data)} bytes")
                    self.rx_buffer.extend(data)
                    self._parse_rx_buffer()

        except (serial.SerialException, OSError) as e:
            self.status_msg.emit(f"Serial error: {str(e)}")
            self.stop_working()
            return

        if self.state == WorkerState.RUNNING:
            QtCore.QTimer.singleShot(0, self._serial_read_step)

    def _parse_rx_buffer(self):
        """
        Parses complete frames from rx_buffer.
        """
        if len(self.rx_buffer) > 4096:
            if TRACE_DECODE:
                print(f"[RX][WARN] buffer overflow, clearing")
            self.rx_buffer.clear()
            return

        while True:
            if len(self.rx_buffer) < 6:
                return

            if self.rx_buffer[0] != MAGIC_0 or self.rx_buffer[1] != MAGIC_1:
                del self.rx_buffer[0]
                continue

            if len(self.rx_buffer) < 5:
                return

            p_type = self.rx_buffer[2]
            p_len = self.rx_buffer[3]
            h_crc = self.rx_buffer[4]

            header = bytes(self.rx_buffer[:4])
            if calculate_crc8(header) != h_crc:
                del self.rx_buffer[0]
                continue

            frame_len = 5 + p_len + 1
            if len(self.rx_buffer) < frame_len:
                return

            payload = bytes(self.rx_buffer[5 : 5 + p_len])
            p_crc = self.rx_buffer[5 + p_len]

            if calculate_crc8(payload) == p_crc:
                self._handle_payload(p_type, payload)
            else:
                if TRACE_DECODE:
                    print("[RX][CRC] payload CRC error")

            del self.rx_buffer[:frame_len]

    def _handle_payload(self, p_type, payload):
        """
        Decodes the binary payload and stores it via data_mgr.
        """
        if not self.frame_decoder or self.active_stream_id is None:
            return

        if p_type != self.active_stream_id:
            return

        if len(payload) != self.frame_decoder.size:
            return

        try:
            decoded = self.frame_decoder.decode(payload)
        except struct.error as e:
            if TRACE_DECODE:
                print(f"[RX] Decode error: {e}")
            return

        if DEBUG_DECODE:
            # debug printing logic here if needed
            pass

        # Delegate storage to Data Manager
        self.data_mgr.store_frame(decoded)

    @QtCore.pyqtSlot(int, int, float, float, float, float, float)
    def send_pid_config(self, ramp_type, motor_id, kp, ki, kff, alpha, rps):
        """
        Sends configuration packet to MCU.
        """
        if not self.serial_port or not self.serial_port.is_open:
            return
        try:
            payload = struct.pack("<BfffffB", motor_id, kp, ki, kff, alpha, rps, ramp_type)
            h_base = struct.pack("BBBB", MAGIC_0, MAGIC_1, RTP_REQ_PID, len(payload))
            h_crc = calculate_crc8(h_base)
            p_crc = calculate_crc8(payload)
            full_frame = h_base + struct.pack("B", h_crc) + payload + struct.pack("B", p_crc)
            self.serial_port.write(full_frame)
        except (serial.SerialTimeoutException, serial.SerialException) as e:
            print(f"Write Error: {e}")

    def _step(self):
        """
        Generates simulated telemetry data for VIRTUAL mode.
        """
        if self.state != WorkerState.RUNNING:
            return

        if self.frame_decoder is None or self.active_stream_id is None:
            return

        loop = self._virtual_loop_cntr
        t = loop * self.sample_period_s

        setpoint = math.sin(t * 0.5)
        measurement = setpoint + random.uniform(-0.05, 0.05)
        measurement_raw = setpoint + random.uniform(-0.08, 0.08)
        error = setpoint - measurement

        decoded = {
            "loopCntr": loop,
            "motor": self.data_mgr.selected_motor,
            "setpoint": setpoint,
            "measurement": measurement,
            "measurementRaw": measurement_raw,
            "error": error,
            "pTerm": error * 20.0,
            "iTerm": math.sin(t * 0.2) * 5.0,
            "outputRaw": error * 30.0,
            "output": max(min(error * 30.0, 100), -100),
        }

        try:
            payload = struct.pack(
                self.frame_decoder.format, *[decoded[f["name"]] for f in self.frame_decoder.fields]
            )
        except (KeyError, struct.error) as e:
            print(f"[VIRTUAL] pack error: {e}")
            return

        self._handle_payload(self.active_stream_id, payload)
        self._virtual_loop_cntr += 1

    def _emit_buffered_data(self):
        """
        Periodic task to fetch processed data from manager and emit to GUI.
        """
        if self.state != WorkerState.RUNNING:
            return

        data = self.data_mgr.get_plot_data(self.sample_period_s)

        if data:
            self.data_ready.emit(data)

    @QtCore.pyqtSlot(float, int)
    def update_time_config(self, period_ms, max_samples):
        self.sample_period_s = period_ms / 1000.0
        self.data_mgr.update_max_samples(max_samples)

        if self.timer and self.timer.isActive():
            self.timer.setInterval(int(period_ms))

    @QtCore.pyqtSlot(dict)
    def configure_signals(self, signals_cfg: dict):
        self.data_mgr.configure(signals_cfg)
        self.state = WorkerState.CONFIGURED
        if DEBUG_DECODE:
            print("[STATE] CONFIGURED")

    def configure_frame(self, stream_cfg: dict):
        if "frame" not in stream_cfg:
            raise ValueError("Stream config missing 'frame' definition")

        frame = stream_cfg["frame"]
        self.frame_decoder = FrameDecoder(
            endian=frame.get("endianness", "little"),
            fields=frame["fields"],
        )
        self.active_stream_id = frame.get("stream_id")

    @QtCore.pyqtSlot(str, float, float)
    def update_scale(self, sig_id, ymin, ymax):
        self.data_mgr.update_scale(sig_id, ymin, ymax)

    @QtCore.pyqtSlot(int)
    def set_selected_motor(self, motor_id: int):
        self.data_mgr.set_motor(motor_id)
