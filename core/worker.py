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
from collections import deque
from enum import Enum, auto
from typing import Deque, Dict, Union

import numpy as np
import serial
from PyQt6 import QtCore

from core.frame_decoder import FrameDecoder
from core.protocol import MAGIC_0, MAGIC_1, RTP_REQ_PID, calculate_crc8

DEBUG_DECODE = False
TRACE_DECODE = False

BufferValue = Union[float, int]


class WorkerState(Enum):
    IDLE = auto()  # No configuration, idle state
    CONFIGURED = auto()  # Signals and frame structure configured
    RUNNING = auto()  # Active data acquisition (Serial or Virtual)


class TelemetryWorker(QtCore.QObject):
    """
    Worker class responsible for handling serial communication and data management.

    This class is designed to run in a separate QThread to prevent freezing the GUI.
    It handles:
    1. Continuous reading from the serial port using a non-blocking recursive loop.
    2. Packet parsing, CRC validation, and data extraction.
    3. Buffering and synchronizing data streams.
    4. Normalizing data for display in PyQtGraph (decoupled via a separate timer).
    5. Sending configuration commands back to the robot.

    Attributes:
        data_ready (pyqtSignal): Emitted when a new batch of data is processed by the GUI timer.
                                 Payload: {'time': np.array, 'signals': dict, 'raw': dict}
        status_msg (pyqtSignal): Emitted to report errors or status updates (str).
    """

    data_ready = QtCore.pyqtSignal(dict)
    status_msg = QtCore.pyqtSignal(str)

    def __init__(self, sample_period_ms: float, max_samples: int):
        super().__init__()
        self.sample_period_s = sample_period_ms / 1000.0
        self.max_samples = max_samples
        self._virtual_loop_cntr: int = 0

        self.rx_buffer = bytearray()

        self.scale = {}

        self.selected_motor = 0
        # buffers structure: buffers[motor_id][signal_id] -> deque
        self.buffers: Dict[int, Dict[str, Deque[BufferValue]]] = {}
        # signals mapping: signal_id -> frame field name
        self.signal_field_map: Dict[str, str] = {}

        self.frame_decoder = None
        self.active_stream_id = None

        self.timer = None  # Timer for Virtual Mode data generation
        self.serial_port = None
        self.state: WorkerState = WorkerState.IDLE

        # --- GUI Update Timer ---
        # Crucial: Passing 'self' as parent ensures this timer moves to the
        # worker thread along with the TelemetryWorker object.
        # This decouples high-frequency serial reading from 30FPS GUI updates.
        self.gui_update_timer = QtCore.QTimer(self)
        self.gui_update_timer.timeout.connect(self._emit_buffered_data)
        self.gui_update_timer.setInterval(33)  # ~30 FPS

    @QtCore.pyqtSlot(str, int)
    def start_working(self, port_name, baudrate):
        """
        Starts the data acquisition process.

        If port_name is 'VIRTUAL', it starts a QTimer for data generation.
        Otherwise, it opens the serial port and initiates the read loop.

        Args:
            port_name (str): The name of the COM port or 'VIRTUAL'.
            baudrate (int): The communication speed.
        """
        if self.state != WorkerState.CONFIGURED:
            self.status_msg.emit("Worker not configured yet")
            if TRACE_DECODE:
                print(f"[STATE] start_working blocked, state={self.state}")
            return

        # Clear buffers to ensure the plot starts fresh (fixes lines jumping back in time)
        self._clear_all_buffers()

        self.state = WorkerState.RUNNING
        if TRACE_DECODE:
            print("[STATE] RUNNING")

        if port_name == "VIRTUAL":
            # Start virtual data generator
            self._virtual_loop_cntr = 0
            if self.timer is None:
                self.timer = QtCore.QTimer(self)
                self.timer.timeout.connect(self._step)
            self.timer.start(int(self.sample_period_s * 1000))
        else:
            try:
                # Open Serial Port
                self.serial_port = serial.Serial(port_name, baudrate, timeout=0.1)
                self.serial_port.reset_input_buffer()
                # Schedule the first read immediately (non-blocking loop)
                QtCore.QTimer.singleShot(0, self._serial_read_step)
            except serial.SerialException as e:
                self.status_msg.emit(f"Connection Error: {e}")
                self.state = WorkerState.CONFIGURED
                return
            except ValueError as e:
                self.status_msg.emit(f"Config Error: {e}")
                self.state = WorkerState.CONFIGURED
                return

        # Start the GUI update loop only after successful connection
        self.gui_update_timer.start()

    @QtCore.pyqtSlot()
    def stop_working(self):
        """
        Stops the worker safely.

        Stops the internal timers, closes the serial port, and sets the running flag to False.
        This slot is typically connected to the GUI disconnection or close events.
        """
        # Stop sending data to GUI immediately
        self.gui_update_timer.stop()

        # Debug trace to identify what triggered the stop
        # traceback.print_stack(limit=5)

        if self.state != WorkerState.RUNNING:
            return

        if TRACE_DECODE:
            print("[STATE] STOP -> CONFIGURED")

        self.state = WorkerState.CONFIGURED

        # Stop virtual generator if active
        if self.timer:
            self.timer.stop()

        # Safely close serial port
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.close()
            except (OSError, serial.SerialException):
                pass
            self.serial_port = None

    def _clear_all_buffers(self):
        """Resets all data buffers to prepare for a new connection session."""
        for motor_bufs in self.buffers.values():
            for buf in motor_bufs.values():
                buf.clear()

    def _serial_read_step(self):
        """
        Performs a single non-blocking read cycle from the serial port.

        This method:
        1. Checks if data is available in the hardware buffer.
        2. Reads all available bytes and appends them to the internal rx_buffer.
        3. Calls the parser.
        4. Re-schedules itself using QTimer.singleShot(0, ...) to yield control
           back to the Qt Event Loop (allowing signals to be processed).
        """
        if self.state != WorkerState.RUNNING:
            return

        # Safety check if port was closed externally
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
            # Handle cable disconnection or driver crash
            self.status_msg.emit(f"Serial error: {str(e)}")
            self.stop_working()
            return

        # Schedule next read immediately if still running
        if self.state == WorkerState.RUNNING:
            QtCore.QTimer.singleShot(0, self._serial_read_step)

    def _parse_rx_buffer(self):
        """
        Parses complete frames from rx_buffer.
        Frame format:
        [MAGIC0][MAGIC1][TYPE][LEN][HDR_CRC][PAYLOAD...][PAYLOAD_CRC]
        """
        # Prevent memory exhaustion if sync is lost
        if len(self.rx_buffer) > 4096:
            if TRACE_DECODE:
                print(f"[RX][WARN] buffer overflow ({len(self.rx_buffer)} bytes), clearing")
            self.rx_buffer.clear()
            return

        while True:
            # minimal frame size = 2 magic + 3 header + 1 payload crc
            if len(self.rx_buffer) < 6:
                return

            # 1. Sync to MAGIC bytes
            if self.rx_buffer[0] != MAGIC_0 or self.rx_buffer[1] != MAGIC_1:
                if TRACE_DECODE:
                    print(
                        f"[RX][SYNC] drop byte 0x{self.rx_buffer[0]:02X}, "
                        f"next=0x{self.rx_buffer[1]:02X}"
                    )
                del self.rx_buffer[0]
                continue

            # 2. Check header availability
            if len(self.rx_buffer) < 5:
                return

            p_type = self.rx_buffer[2]
            p_len = self.rx_buffer[3]
            h_crc = self.rx_buffer[4]

            # 3. Validate Header CRC
            header = bytes(self.rx_buffer[:4])
            if calculate_crc8(header) != h_crc:
                # bad header → drop byte and resync
                del self.rx_buffer[0]
                continue

            # 4. Check if full payload is available
            frame_len = 5 + p_len + 1
            if len(self.rx_buffer) < frame_len:
                return  # wait for more data

            payload = bytes(self.rx_buffer[5 : 5 + p_len])
            p_crc = self.rx_buffer[5 + p_len]

            # 5. Validate Payload CRC
            if calculate_crc8(payload) == p_crc:
                if TRACE_DECODE:
                    print(f"[RX][FRAME] type={p_type} len={p_len}")
                self._handle_payload(p_type, payload)
            else:
                if TRACE_DECODE:
                    print("[RX][CRC] payload CRC error")

            # 6. Consume processed frame
            del self.rx_buffer[:frame_len]

    def _handle_payload(self, p_type, payload):
        """
        Decodes the binary payload based on the packet type and updates buffers.

        Args:
            p_type (int): The packet ID (e.g., RTP_PID).
            payload (bytes): The raw data bytes of the packet body.
        """
        if not self.frame_decoder or self.active_stream_id is None:
            return

        # Ignore payloads that do not match the configured stream ID
        if p_type != self.active_stream_id:
            return

        # Validate expected length matches decoder config
        if len(payload) != self.frame_decoder.size:
            return

        try:
            decoded = self.frame_decoder.decode(payload)
        except struct.error as e:
            if TRACE_DECODE:
                print(f"[RX] Decode error: {e}")
            return

        if DEBUG_DECODE:
            items = []
            if decoded["motor"] == 0:
                for k, v in decoded.items():
                    if isinstance(v, float):
                        items.append(f"{k}={v:.5f}")
                    else:
                        items.append(f"{k}={v}")
                print("[RX][DATA] " + " ".join(items))

        # --- MOTOR FILTER & BUFFERING ---
        # Only buffers are updated here. No expensive math or emitting is done.
        motor = decoded.get("motor")
        if motor not in self.buffers:
            if TRACE_DECODE:
                print(f"[RX] Motor {motor} not configured, buffers={list(self.buffers.keys())}")
            return

        loop_cntr = decoded["loopCntr"]
        motor_buffers = self.buffers[motor]

        # Store time base (loop counter)
        motor_buffers["__loop__"].append(loop_cntr)

        # Store signals based on mapping
        for sig_id, field in self.signal_field_map.items():
            motor_buffers[sig_id].append(decoded[field])

    @QtCore.pyqtSlot(int, int, float, float, float, float, float)
    def send_pid_config(self, ramp_type, motor_id, kp, ki, kff, alpha, rps):
        """
        Constructs and sends a PID configuration packet to the robot.

        Args:
            motor_id (int): ID of the motor (0: Left, 1: Right, 2: Both).
            kp (float): Proportional gain.
            ki (float): Integral gain.
            kff (float): Feed-forward gain.
            alpha (float): Filter alpha.
            rps (float): Target revolutions per second.
            ramp_type (int): 0 for instant setpoint, 1 for ramped.
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
        except serial.SerialTimeoutException:
            print("Write Timeout")
        except serial.SerialException as e:
            print(f"Write Error (Connection lost?): {e}")

    def _step(self):
        """
        Generates simulated telemetry data for VIRTUAL mode.
        Mimics MCU behavior by creating fake sine waves and noise.
        """
        if self.state != WorkerState.RUNNING:
            return

        if self.frame_decoder is None or self.active_stream_id is None:
            return

        loop = self._virtual_loop_cntr
        t = loop * self.sample_period_s

        # --- Simulate signals ---
        setpoint = math.sin(t * 0.5)
        measurement = setpoint + random.uniform(-0.05, 0.05)
        measurement_raw = setpoint + random.uniform(-0.08, 0.08)
        error = setpoint - measurement

        decoded = {
            "loopCntr": loop,
            "motor": self.selected_motor,
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
        Periodic task (triggered by gui_update_timer) to process and emit data to GUI.

        This method performs the "heavy lifting":
        1. Converts Python Deques to Numpy Arrays (memory copy).
        2. Normalizes signals to 0.0-1.0 range for the ViewBox.
        3. Emits the `data_ready` signal.

        This decoupling prevents the high-frequency Serial loop from blocking the Main Thread.
        """
        # 1. Pre-checks
        if self.state != WorkerState.RUNNING:
            return

        if self.selected_motor not in self.buffers:
            return

        motor_buffers = self.buffers[self.selected_motor]
        loops_cntr = motor_buffers["__loop__"]

        if len(loops_cntr) < 2:
            return

        # 2. Build time axis
        loop_cntr_arr = np.asarray(loops_cntr, dtype=float)
        time_axis = loop_cntr_arr * self.sample_period_s

        snapshot_raw: Dict[str, np.ndarray] = {}
        out_norm: Dict[str, np.ndarray] = {}

        # 3. Process signals
        for sig_id, buf in motor_buffers.items():
            if sig_id == "__loop__":
                continue

            # Convert to numpy array (expensive operation)
            arr = np.asarray(buf, dtype=float)

            # Sanity check: ensure array lengths match time axis
            if len(arr) != len(time_axis):
                continue

            # Keep raw data for tooltips/analysis
            snapshot_raw[sig_id] = arr

            # Normalize data for visualization (ViewBox uses 0.0 - 1.0 range)
            if sig_id in self.scale:
                ymin, ymax = self.scale[sig_id]
                scale = max(ymax - ymin, 1e-12)
                out_norm[sig_id] = np.clip((arr - ymin) / scale, 0.0, 1.0)

        # 4. Emit to Main Thread
        self.data_ready.emit(
            {
                "time": time_axis,
                "signals": out_norm,
                "raw": snapshot_raw,
            }
        )

    @QtCore.pyqtSlot(float, int)
    def update_time_config(self, period_ms, max_samples):
        """
        Updates the sampling configuration and resizes buffers accordingly.
        """
        self.sample_period_s = period_ms / 1000.0
        self.max_samples = max_samples

        # Rebuild all deques with new maxlen
        for motor_bufs in self.buffers.values():
            for sig_id, old_buf in motor_bufs.items():
                motor_bufs[sig_id] = deque(old_buf, maxlen=max_samples)

        # Update virtual timer if active
        if self.timer and self.timer.isActive():
            self.timer.setInterval(int(period_ms))

    @QtCore.pyqtSlot(dict)
    def configure_signals(self, signals_cfg: dict):
        """
        Initializes signal buffers based on the configuration.
        Clears existing data and sets up mappings.
        """
        self.buffers.clear()
        self.scale.clear()
        self.signal_field_map.clear()

        # Build signal → frame field mapping
        for sig_id, sig in signals_cfg.items():
            self.signal_field_map[sig_id] = sig["field"]
            yr = sig["y_range"]
            self.scale[sig_id] = (yr["min"], yr["max"])

        # Prepare buffers for known motors (0: Left, 1: Right)
        for motor_id in (0, 1):
            motor_bufs: Dict[str, Deque[float]] = {}
            motor_bufs["__loop__"] = deque(maxlen=self.max_samples)

            for sig_id in signals_cfg.keys():
                motor_bufs[sig_id] = deque(maxlen=self.max_samples)

            self.buffers[motor_id] = motor_bufs

        self.state = WorkerState.CONFIGURED
        if DEBUG_DECODE:
            print("[STATE] CONFIGURED")

    def configure_frame(self, stream_cfg: dict):
        """
        Configures the FrameDecoder based on the 'frame' section of the stream config.
        """
        if "frame" not in stream_cfg:
            raise ValueError("Stream config missing 'frame' definition")

        frame = stream_cfg["frame"]

        self.frame_decoder = FrameDecoder(
            endian=frame.get("endianness", "little"),
            fields=frame["fields"],
        )

        self.active_stream_id = frame.get("stream_id")

        if DEBUG_DECODE:
            print(
                f"[CFG] Active stream set: stream_id={self.active_stream_id}, "
                f"bytes={self.frame_decoder.size}"
            )

    @QtCore.pyqtSlot(str, float, float)
    def update_scale(self, sig_id, ymin, ymax):
        """Updates the Y-axis scaling range for a specific signal."""
        if sig_id in self.scale:
            self.scale[sig_id] = (ymin, ymax)

    @QtCore.pyqtSlot(int)
    def set_selected_motor(self, motor_id: int):
        """
        Sets the active motor ID and clears buffers to prevent data mixing.
        """
        if motor_id not in self.buffers:
            return

        self.selected_motor = motor_id

        # Clear active buffers to avoid plotting old data from the previous motor
        for buf in self.buffers[motor_id].values():
            buf.clear()
