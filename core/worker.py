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
    IDLE = auto()  # brak konfiguracji, brak pracy
    CONFIGURED = auto()  # sygnały + frame skonfigurowane
    RUNNING = auto()  # aktywne czytanie (serial lub virtual)


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
        self._virtual_loop_cntr: int = 0

        self.rx_buffer = bytearray()

        self.scale = {}

        self.selected_motor = 0
        # buffers[motor_id][signal_id] -> deque
        self.buffers: Dict[int, Dict[str, Deque[BufferValue]]] = {}
        # signals mapping: signal_id -> frame field name
        self.signal_field_map: Dict[str, str] = {}

        self.frame_decoder = None
        self.active_stream_id = None

        self.timer = None
        self.serial_port = None
        self.state: WorkerState = WorkerState.IDLE

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
            port_name (str): The name of the COM port (e.g., 'COM3', '/dev/ttyUSB0') or 'VIRTUAL'.
            baudrate (int): The communication speed.
        """
        self.gui_update_timer.start()
        if self.state != WorkerState.CONFIGURED:
            self.status_msg.emit("Worker not configured yet")
            if TRACE_DECODE:
                print(f"[STATE] start_working blocked, state={self.state}")
            return

        self._clear_all_buffers()

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
                # Otwieramy port
                self.serial_port = serial.Serial(port_name, baudrate, timeout=0.1)
                self.serial_port.reset_input_buffer()
                # Zamiast blokującej pętli while, uruchamiamy pierwszy krok
                QtCore.QTimer.singleShot(0, self._serial_read_step)
            except serial.SerialException as e:
                self.status_msg.emit(f"Connection Error: {e}")
                self.state = WorkerState.CONFIGURED
            except ValueError as e:
                self.status_msg.emit(f"Config Error: {e}")
                self.state = WorkerState.CONFIGURED
        self.gui_update_timer.start()

    @QtCore.pyqtSlot()
    def stop_working(self):
        """
        Stops the worker safely.

        Stops the internal timer, closes the serial port, and sets the running flag to False.
        This slot is typically connected to the GUI disconnection or close events.
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

    def _clear_all_buffers(self):
        for motor_bufs in self.buffers.values():
            for buf in motor_bufs.values():
                buf.clear()

    def _serial_read_step(self):
        """
        Performs a single non-blocking read cycle from the serial port.

        This method reads available bytes, searches for magic numbers, parses headers,
        and validates CRCs. It uses `QTimer.singleShot(0, self._serial_read_step)`
        to schedule itself recursively. This approach allows the Qt Event Loop to
        process other events (like signals) between reads, ensuring the thread
        remains responsive to 'stop' commands.
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
                    if TRACE_DECODE:
                        print(f"[RX][BUF] size={len(self.rx_buffer)}")
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
        Frame format:
        [MAGIC0][MAGIC1][TYPE][LEN][HDR_CRC][PAYLOAD...][PAYLOAD_CRC]
        """
        if len(self.rx_buffer) > 4096:
            if TRACE_DECODE:
                print(f"[RX][WARN] buffer overflow ({len(self.rx_buffer)} bytes), clearing")
            self.rx_buffer.clear()
            return
        while True:
            # minimal frame = 2 magic + 3 header + 1 payload crc
            if len(self.rx_buffer) < 6:
                return

            # sync to MAGIC
            if self.rx_buffer[0] != MAGIC_0 or self.rx_buffer[1] != MAGIC_1:
                if TRACE_DECODE:
                    print(
                        f"[RX][SYNC] drop byte 0x{self.rx_buffer[0]:02X}, "
                        f"next=0x{self.rx_buffer[1]:02X}"
                    )
                del self.rx_buffer[0]
                continue

            # header available?
            if len(self.rx_buffer) < 5:
                return

            p_type = self.rx_buffer[2]
            p_len = self.rx_buffer[3]
            h_crc = self.rx_buffer[4]

            header = bytes(self.rx_buffer[:4])
            if calculate_crc8(header) != h_crc:
                # bad header → resync
                del self.rx_buffer[0]
                continue

            frame_len = 5 + p_len + 1
            if len(self.rx_buffer) < frame_len:
                return  # wait for more data

            payload = bytes(self.rx_buffer[5 : 5 + p_len])
            p_crc = self.rx_buffer[5 + p_len]

            if calculate_crc8(payload) == p_crc:
                if TRACE_DECODE:
                    print(f"[RX][FRAME] type={p_type} len={p_len}")
                self._handle_payload(p_type, payload)
            else:
                if TRACE_DECODE:
                    print("[RX][CRC] payload CRC error")

            # consume frame
            del self.rx_buffer[:frame_len]
            if TRACE_DECODE:
                print(f"[RX][BUF] consumed frame, remaining={len(self.rx_buffer)}")

    def _handle_payload(self, p_type, payload):
        """
        Decodes the binary payload based on the packet type.

        Args:
            p_type (int): The packet ID (e.g., RTP_PID).
            payload (bytes): The raw data bytes of the packet body.
        """
        if TRACE_DECODE:
            print(f"[RX] got payload type={p_type}, len={len(payload)}")

        if not self.frame_decoder or self.active_stream_id is None:
            return

        # Ignore payloads for other stream ID
        if p_type != self.active_stream_id:
            if TRACE_DECODE:
                print(f"[RX] Ignored payload type={p_type}, active={self.active_stream_id}")
            return

        # Validate length
        if len(payload) != self.frame_decoder.size:
            if TRACE_DECODE:
                print(
                    f"[RX] Payload size mismatch for stream_id={p_type}: "
                    f"got={len(payload)} expected={self.frame_decoder.size}"
                )
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

        # --- MOTOR FILTER
        motor = decoded.get("motor")
        if motor not in self.buffers:
            if TRACE_DECODE:
                print(f"[RX] Motor {motor} not configured, buffers={list(self.buffers.keys())}")
            return

        loop_cntr = decoded["loopCntr"]

        # Store values in motor-specific buffers
        motor_buffers = self.buffers[motor]
        # store time base
        motor_buffers["__loop__"].append(loop_cntr)

        # store signals
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
            alpha (float): Filter alpha
            rps (float): rps of the motor to spin
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
        Uses the SAME decode path as real serial data.
        """

        if self.state != WorkerState.RUNNING:
            return

        if self.frame_decoder is None or self.active_stream_id is None:
            return  # not configured yet

        loop = self._virtual_loop_cntr
        t = loop * self.sample_period_s

        # --- simulate signals ---
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
        # 1. Sprawdzenia wstępne (czy Worker działa, czy jest wybrany silnik)
        if self.state != WorkerState.RUNNING:
            return

        if self.selected_motor not in self.buffers:
            return

        motor_buffers = self.buffers[self.selected_motor]
        loops_cntr = motor_buffers["__loop__"]

        if len(loops_cntr) < 2:
            return

        # 2. Budowanie osi czasu (to już miałeś)
        loop_cntr_arr = np.asarray(loops_cntr, dtype=float)
        time_axis = loop_cntr_arr * self.sample_period_s

        snapshot_raw: Dict[str, np.ndarray] = {}
        out_norm: Dict[str, np.ndarray] = {}

        # Iterujemy po każdym sygnale w buforze silnika
        for sig_id, buf in motor_buffers.items():
            # Pomijamy bufor czasu/licznika, bo obsłużyliśmy go wyżej
            if sig_id == "__loop__":
                continue

            # Konwersja deque na numpy array (to jest operacja kosztowna, dlatego robimy ją w timerze)
            arr = np.asarray(buf, dtype=float)

            # Zabezpieczenie: jeśli długość sygnału różni się od osi czasu, pomijamy, żeby nie wysypać wykresu
            if len(arr) != len(time_axis):
                continue

            # Zapisujemy surowe dane (potrzebne do tooltipów i analizy po pauzie)
            snapshot_raw[sig_id] = arr

            # Pobieramy zakres skalowania (min, max) dla danego sygnału
            # self.scale jest słownikiem wypełnianym przy konfiguracji
            if sig_id in self.scale:
                ymin, ymax = self.scale[sig_id]

                # Obliczamy skalę, zabezpieczając się przed dzieleniem przez zero
                scale = max(ymax - ymin, 1e-12)

                # Normalizujemy dane do zakresu 0.0 - 1.0 (dla pyqtgraph ViewBox)
                # np.clip ucina wartości, które wykraczają poza zakres
                out_norm[sig_id] = np.clip((arr - ymin) / scale, 0.0, 1.0)

        # 3. Emitowanie gotowej paczki do UI
        self.data_ready.emit(
            {
                "time": time_axis,
                "signals": out_norm,
                "raw": snapshot_raw,
            }
        )

    @QtCore.pyqtSlot(float, int)
    def update_time_config(self, period_ms, max_samples):
        """Updates the sampling configuration and resets buffers."""
        self.sample_period_s = period_ms / 1000.0
        self.max_samples = max_samples

        # rebuild all deques with new maxlen
        for motor_bufs in self.buffers.values():
            for sig_id, old_buf in motor_bufs.items():
                motor_bufs[sig_id] = deque(old_buf, maxlen=max_samples)

        if self.timer and self.timer.isActive():
            self.timer.setInterval(int(period_ms))

    @QtCore.pyqtSlot(dict)
    def configure_signals(self, signals_cfg: dict):
        """
        Initialize per-motor buffers.
        """
        self.buffers.clear()
        self.scale.clear()
        self.signal_field_map.clear()

        # Build signal → frame field mapping
        for sig_id, sig in signals_cfg.items():
            self.signal_field_map[sig_id] = sig["field"]
            yr = sig["y_range"]
            self.scale[sig_id] = (yr["min"], yr["max"])

        # Prepare buffers for known motors (0,1)
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
                f"bytes={self.frame_decoder.size}, "
                f"fields={[f['name'] for f in frame['fields']]}"
            )

    @QtCore.pyqtSlot(str, float, float)
    def update_scale(self, sig_id, ymin, ymax):
        """Updates the Y-axis scaling range for a specific signal."""
        if sig_id in self.scale:
            self.scale[sig_id] = (ymin, ymax)

    def _debug_print_frame(self, stream_name: str, values: dict):
        if not DEBUG_DECODE:
            return

        items = " ".join(
            f"{k}={v:.4f}" if isinstance(v, float) else f"{k}={v}" for k, v in values.items()
        )

        # print(f"[RX][{stream_name}] {items}")

    @QtCore.pyqtSlot(int)
    def set_selected_motor(self, motor_id: int):
        """
        Ustawia ID silnika i czyści bufory, aby uniknąć mieszania danych.
        """
        if motor_id not in self.buffers:
            return

        self.selected_motor = motor_id

        # Czyścimy wszystkie aktywne bufory sygnałów
        for buf in self.buffers[motor_id].values():
            buf.clear()

        # print(f"[UI] Active motor set to {motor_id}")
