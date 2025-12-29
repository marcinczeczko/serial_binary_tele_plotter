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
import traceback
from collections import deque

import numpy as np
import serial
from PyQt6 import QtCore

from core.protocol import MAGIC_0, MAGIC_1, RTP_PID, RTP_REQ_PID, calculate_crc8


class TelemetryWorker(QtCore.QObject):
    """
    Worker class responsible for handling serial communication and data management.

    This class is designed to run in a separate QThread to prevent freezing the GUI.
    It handles:
    1. Continuous reading from the serial port using a non-blocking recursive loop.
    2. Packet parsing, CRC validation, and data extraction.
    3. Buffering and synchronizing data streams.
    4. Normalizing data for display in PyQtGraph.
    5. Sending configuration commands back to the robot.

    Attributes:
        data_ready (pyqtSignal): Emitted when a new batch of data is processed.
                                 Payload: {'time': np.array, 'signals': dict, 'raw': dict}
        status_msg (pyqtSignal): Emitted to report errors or status updates (str).
    """

    data_ready = QtCore.pyqtSignal(dict)
    status_msg = QtCore.pyqtSignal(str)

    def __init__(self, sample_period_ms: float, max_samples: int):
        super().__init__()
        self.sample_period_s = sample_period_ms / 1000.0
        self.max_samples = max_samples
        self.sample_index = 0
        self.buffers = {}
        self.scale = {}

        self.timer = None
        self.serial_port = None
        self.is_running = False

    @QtCore.pyqtSlot(str, int)
    def start_working(self, port_name, baudrate):
        """
        Starts the data acquisition process.

        If port_name is 'VIRTUAL', it starts a QTimer for data generation.
        Otherwise, it opens the serial port and initiates the read loop.

        Args:
            port_name (str): The name of the COM port (e.g., 'COM3', '/dev/ttyUSB0') or 'VIRTUAL'.
            baudrate (int): The communication speed.
        """
        self.is_running = True

        if port_name == "VIRTUAL":
            if self.timer is None:
                self.timer = QtCore.QTimer(self)
                self.timer.timeout.connect(self._step)
            self.timer.start(int(self.sample_period_s * 1000))
        else:
            try:
                # Otwieramy port
                self.serial_port = serial.Serial(port_name, baudrate, timeout=0.1)
                self.serial_port.reset_input_buffer()
                # Zamiast blokującej pętli while, uruchamiamy pierwszy krok
                QtCore.QTimer.singleShot(0, self._serial_read_step)
            except serial.SerialException as e:
                self.status_msg.emit(f"Connection Error: {e}")
                self.is_running = False
            except ValueError as e:
                self.status_msg.emit(f"Config Error: {e}")
                self.is_running = False

    @QtCore.pyqtSlot()
    def stop_working(self):
        """
        Stops the worker safely.

        Stops the internal timer, closes the serial port, and sets the running flag to False.
        This slot is typically connected to the GUI disconnection or close events.
        """
        self.is_running = False

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

        This method reads available bytes, searches for magic numbers, parses headers,
        and validates CRCs. It uses `QTimer.singleShot(0, self._serial_read_step)`
        to schedule itself recursively. This approach allows the Qt Event Loop to
        process other events (like signals) between reads, ensuring the thread
        remains responsive to 'stop' commands.
        """
        if not self.is_running or not self.serial_port:
            return

        try:
            # Read everything in buffer to avoid lag
            # Limit iterations to avoid freezing UI during data floods
            iterations = 0
            while self.serial_port and self.serial_port.in_waiting >= 5 and iterations < 50:
                iterations += 1

                # Search for header synchronization (byte by byte)
                b = self.serial_port.read(1)
                if not b or ord(b) != MAGIC_0:
                    continue

                b = self.serial_port.read(1)
                if not b or ord(b) != MAGIC_1:
                    continue

                # Read rest of the header
                h_data = self.serial_port.read(3)
                if len(h_data) < 3:
                    continue

                p_type, p_len, h_crc = struct.unpack("BBB", h_data)

                # Verify Header CRC
                header_check = struct.pack("BBBB", MAGIC_0, MAGIC_1, p_type, p_len)
                if calculate_crc8(header_check) != h_crc:
                    continue

                # Read Payload
                payload = self.serial_port.read(p_len)
                if len(payload) != p_len:
                    continue

                # Read and Verify Payload CRC
                p_crc_raw = self.serial_port.read(1)
                if len(p_crc_raw) < 1:
                    continue
                p_crc = ord(p_crc_raw)

                if calculate_crc8(payload) == p_crc:
                    self._handle_payload(p_type, payload)
        except (serial.SerialException, OSError) as e:
            self.status_msg.emit(f"Device Disconnected: {e}")
            self.stop_working()
            return
        except Exception as e:
            print(f"CRITICAL WORKER ERROR: {e}")
            traceback.print_exc()  # Wypisze pełny stack trace
            self.stop_working()
            return

        # Schedule next read immediately if still running
        if self.is_running:
            QtCore.QTimer.singleShot(0, self._serial_read_step)

    def _handle_payload(self, p_type, payload):
        """
        Decodes the binary payload based on the packet type.

        Args:
            p_type (int): The packet ID (e.g., RTP_PID).
            payload (bytes): The raw data bytes of the packet body.
        """
        if p_type == RTP_PID:
            try:
                # Unpack: timestamp (uint32), reserved (byte), 7 floats
                d = struct.unpack("<IBfffffff", payload)
                t = d[0] * self.sample_period_s
                values = {
                    "setpoint": d[2],
                    "measurement": d[3],
                    "error": d[4],
                    "p_term": d[5],
                    "i_term": d[6],
                    "output": d[8],
                }
                self._update_buffers(values, t)
            except struct.error:
                pass

    @QtCore.pyqtSlot(int, float, float, float)
    def send_pid_config(self, motor_id, kp, ki, kff):
        """
        Constructs and sends a PID configuration packet to the robot.

        Args:
            motor_id (int): ID of the motor (0: Left, 1: Right, 2: Both).
            kp (float): Proportional gain.
            ki (float): Integral gain.
            kff (float): Feed-forward gain.
        """
        if not self.serial_port or not self.serial_port.is_open:
            return
        try:
            payload = struct.pack("<Bfff", motor_id, kp, ki, kff)
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
        """
        t = self.sample_index * self.sample_period_s
        setpoint = math.sin(t * 0.5)
        measurement = setpoint + random.uniform(-0.05, 0.05)
        error = setpoint - measurement
        values = {
            "setpoint": setpoint,
            "measurement": measurement,
            "error": error,
            "p_term": error * 20.0,
            "i_term": math.sin(t * 0.2) * 5.0,
            "output": max(min(error * 30.0, 100), -100),
        }
        self._update_buffers(values, t)
        self.sample_index += 1

    def _update_buffers(self, values, current_time):
        """
        Updates internal buffers, synchronizes data lengths, and prepares signals for the UI.

        This method:
        1. Appends new values to deques.
        2. Creates a snapshot (list) of the data to avoid race conditions.
        3. Synchronizes all signals to the minimum available length.
        4. Normalizes signals (0.0 - 1.0) based on configured ranges.
        5. Emits the `data_ready` signal.

        Args:
            values (dict): New data points keyed by signal ID.
            current_time (float): The timestamp associated with this data batch.
        """
        # 1. Append new data
        for k, v in values.items():
            if k in self.buffers:
                self.buffers[k].append(v)

        if not self.buffers:
            return

        # 2. Snapshot to avoid modification during drawing
        # Get only buffers that actually have data
        snapshot_raw = {}
        active_lengths = []

        for sig_id, buf in self.buffers.items():
            data_list = list(buf)
            if len(data_list) > 0:
                snapshot_raw[sig_id] = np.array(data_list)
                active_lengths.append(len(data_list))

        # If no data or not enough samples, exit
        if not active_lengths or max(active_lengths) < 2:
            return

        # 3. Synchronize lengths (take minimum of active buffers)
        min_len = min(active_lengths)
        for sig_id, arr in snapshot_raw.items():
            if len(arr) > min_len:
                snapshot_raw[sig_id] = arr[:min_len]

        # 4. Create time axis
        time_axis = np.linspace(
            current_time - min_len * self.sample_period_s, current_time, min_len
        )

        # 5. Normalize
        out_signals_norm = {}
        for sig_id, arr in snapshot_raw.items():
            if sig_id in self.scale:
                ymin, ymax = self.scale[sig_id]
                scale = max(ymax - ymin, 1e-12)
                out_signals_norm[sig_id] = np.clip((arr - ymin) / scale, 0.0, 1.0)

        self.data_ready.emit({"time": time_axis, "signals": out_signals_norm, "raw": snapshot_raw})

    @QtCore.pyqtSlot(float, int)
    def update_time_config(self, period_ms, max_samples):
        """Updates the sampling configuration and resets buffers."""
        self.sample_period_s = period_ms / 1000.0
        self.max_samples = max_samples

        self.buffers = {k: deque(maxlen=self.max_samples) for k in self.buffers}
        if self.timer and self.timer.isActive():
            self.timer.setInterval(int(period_ms))

    @QtCore.pyqtSlot(dict)
    def configure_signals(self, signals_cfg: dict):
        """Resets and re-initializes buffers based on the new signal configuration."""
        self.buffers.clear()
        self.scale.clear()
        for sig_id, sig in signals_cfg.items():
            self.buffers[sig_id] = deque(maxlen=self.max_samples)
            yr = sig["y_range"]
            self.scale[sig_id] = (yr["min"], yr["max"])
        self.sample_index = 0

    @QtCore.pyqtSlot(str, float, float)
    def update_scale(self, sig_id, ymin, ymax):
        """Updates the Y-axis scaling range for a specific signal."""
        if sig_id in self.scale:
            self.scale[sig_id] = (ymin, ymax)
