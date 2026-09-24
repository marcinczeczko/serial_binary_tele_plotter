"""
Background engine module for telemetry data processing.

The engine runs in its own QThread and orchestrates:
1. Input: a `Transport` drained by a dedicated `ReaderThread` (blocking reads, P5), or
   the virtual simulator.
2. Protocol: parsing and decoding. This happens on the reader thread under `_data_lock`.
3. Storage: a `SampleStore` (versioned ring buffer). The GUI pulls from it at its own
   frame rate (ADR-0002, R2.6), so the engine never pushes bulk data through Qt.
4. GUI output: state, status and link statistics (small signals only).

Threads: the reader thread uses the parser under `_data_lock`, and the engine thread
takes the same lock for stats and reconfiguration. The store has its own lock and is
safe to call from any thread.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from PyQt6 import QtCore

from core.acquisition.storage import SampleStore
from core.acquisition.virtual import VirtualDevice
from core.protocol.handler import ProtocolHandler
from core.protocol.stats import LinkStats, make_link_report
from core.transport import ReaderThread, SerialTransport, Transport, TransportError
from core.types import DecodedFrame, EngineState, SignalsConfig, StreamConfig

logger = logging.getLogger(__name__)


class TelemetryEngine(QtCore.QObject):
    """
    The main controller class for the background thread.

    The engine owns its lifecycle state machine (IDLE -> CONFIGURED <-> RUNNING). All
    decisions that depend on the current state, such as whether to restart after a stream
    switch, are made here on the engine thread. The GUI only mirrors `state_changed`.
    """

    state_changed = QtCore.pyqtSignal(object)  # EngineState
    link_stats = QtCore.pyqtSignal(dict)  # LinkReport, ~1 Hz while running
    status_msg = QtCore.pyqtSignal(str)
    connection_failed = QtCore.pyqtSignal(str)
    # Emitted from the reader thread; delivered to `_on_reader_failed` on the engine thread.
    _reader_failed = QtCore.pyqtSignal(str)

    def __init__(
        self, sample_period_ms: float, max_samples: int, store: SampleStore | None = None
    ) -> None:
        super().__init__()
        self.sample_period_s: float = sample_period_ms / 1000.0

        # Shared with the GUI, which reads snapshots from it directly (thread-safe).
        self.store: SampleStore = store if store is not None else SampleStore(max_samples)
        self.protocol: ProtocolHandler = ProtocolHandler()

        # 'parent=self' is crucial here! It ensures that when TelemetryEngine is moved
        # to a new QThread, the VirtualDevice (and its internal QTimer) moves with it.
        self.virtual = VirtualDevice(parent=self)
        self.virtual.frame_generated.connect(self._store_virtual_frame)

        # --- IO & State ---
        # Creates the transport for a port name; tests swap in fakes.
        self.transport_factory: Callable[[str, int], Transport] = SerialTransport
        self._transport: Transport | None = None
        self._reader: ReaderThread | None = None
        self._data_lock = threading.Lock()
        self._reader_failed.connect(self._on_reader_failed)
        self.state: EngineState = EngineState.IDLE
        # Last successfully opened connection; used to restart after a stream switch.
        self._port: str | None = None
        self._baud: int = 0

        # --- Link statistics (C13) ---
        self.stats_timer: QtCore.QTimer = QtCore.QTimer(self)
        self.stats_timer.timeout.connect(self._emit_link_stats)
        self.stats_timer.setInterval(1000)
        self._stats_prev: LinkStats = LinkStats()
        self._stats_prev_samples: int = 0
        self._stats_prev_ts: float = 0.0

    @QtCore.pyqtSlot(str, int)
    def start_working(self, port_name: str, baudrate: int) -> None:
        """Initiates the data acquisition process."""
        if self.state == EngineState.RUNNING:
            self.status_msg.emit("Already running")
            return
        if self.state != EngineState.CONFIGURED:
            self.status_msg.emit("No stream configured yet")
            return

        # Clear buffers to prevent "time travel" artifacts
        with self._data_lock:
            self.store.clear()
            self.protocol.reset()
            self._stats_prev = self.protocol.stats.snapshot()
            self._stats_prev_samples = self.store.total_stored
        self._stats_prev_ts = time.monotonic()

        if port_name == "VIRTUAL":
            self.virtual.start(self.sample_period_s)
        else:
            transport = self.transport_factory(port_name, baudrate)
            try:
                transport.open()
            except TransportError as e:
                msg = f"Connection Error: {e}"
                self.status_msg.emit(msg)
                self.connection_failed.emit(msg)
                return
            self._transport = transport

        self._port, self._baud = port_name, baudrate
        self._set_state(EngineState.RUNNING)
        self.stats_timer.start()
        self.status_msg.emit(f"Connected to {port_name}")
        # Start reading only once RUNNING, so an immediate failure is never ignored.
        if self._transport is not None:
            self._reader = ReaderThread(self._transport, self._on_bytes, self._reader_failed.emit)
            self._reader.start()

    @QtCore.pyqtSlot()
    def stop_working(self) -> None:
        """Safely stops all operations."""
        self.stats_timer.stop()

        if self.state != EngineState.RUNNING:
            return

        self._set_state(EngineState.CONFIGURED)
        self.virtual.stop()
        self._close_transport()

    def _close_transport(self) -> None:
        reader, self._reader = self._reader, None
        transport, self._transport = self._transport, None
        if reader is not None:
            reader.stop()
        if transport is not None:
            transport.close()

    @QtCore.pyqtSlot(dict)
    def select_stream(self, stream_cfg: StreamConfig) -> None:
        """
        Switches to another stream definition atomically on the engine thread.

        If acquisition is running it is stopped, reconfigured and restarted on the same
        port, so a stream switch never leaves the engine half-configured (C5).
        """
        was_running = self.state == EngineState.RUNNING
        if was_running:
            self.stop_working()
        self.configure_signals(stream_cfg.get("signals", {}))
        self.configure_frame(stream_cfg)
        if was_running and self._port is not None:
            self.start_working(self._port, self._baud)

    def _set_state(self, state: EngineState) -> None:
        if state != self.state:
            self.state = state
            self.state_changed.emit(state)

    def _write(self, packet: bytes) -> None:
        """Sends a command packet; a failure (incl. write timeout, C9) is reported, not fatal."""
        transport = self._transport
        if transport is None:
            self.status_msg.emit("Not connected to a serial port: command not sent")
            return
        try:
            transport.write(packet)
        except TransportError as e:
            logger.warning("Serial write error: %s", e)
            self.status_msg.emit(f"Write Error: {e}")

    def _on_bytes(self, data: bytes) -> None:
        """Reader-thread callback: parses one chunk and stores its frames."""
        with self._data_lock:
            self.protocol.add_data(data)
            frames = list(self.protocol.process_available_frames())
        self.store.append(frames)

    def _store_virtual_frame(self, frame: DecodedFrame) -> None:
        self.store.append((frame,))

    @QtCore.pyqtSlot(str)
    def _on_reader_failed(self, message: str) -> None:
        """Engine-thread handler for a transport failure reported by the reader thread."""
        if self.state != EngineState.RUNNING:
            return
        msg = f"Serial error: {message}"
        self.status_msg.emit(msg)
        self.connection_failed.emit(msg)
        self.stop_working()

    def _emit_link_stats(self) -> None:
        """Periodic task (stats_timer): emits counters and rates since the previous report."""
        now = time.monotonic()
        with self._data_lock:
            cur = self.protocol.stats.snapshot()
            samples = self.store.total_stored
        report = make_link_report(
            self._stats_prev, cur, samples - self._stats_prev_samples, now - self._stats_prev_ts
        )
        self._stats_prev, self._stats_prev_samples, self._stats_prev_ts = cur, samples, now
        self.link_stats.emit(report)

    @QtCore.pyqtSlot(float, int)
    def update_time_config(self, period_ms: float, max_samples: int) -> None:
        """Updates sampling settings and resizes buffers."""
        self.sample_period_s = period_ms / 1000.0
        self.store.resize(max_samples)
        self.virtual.update_params(self.sample_period_s)

    @QtCore.pyqtSlot(dict)
    def configure_signals(self, signals_cfg: SignalsConfig) -> None:
        """Configures the Data Manager with the signal definitions."""
        self.store.configure(signals_cfg)
        # Never demote RUNNING here: that silently stalled acquisition with the port open (C5).
        if self.state == EngineState.IDLE:
            self._set_state(EngineState.CONFIGURED)

    @QtCore.pyqtSlot(dict)
    def configure_frame(self, stream_cfg: StreamConfig) -> None:
        """Configures the Protocol Handler with the binary frame structure."""
        try:
            with self._data_lock:
                self.protocol.configure(stream_cfg)
            name = stream_cfg.get("name", "Unknown")
            self.virtual.configure_stream(name)
        except ValueError as e:
            self.status_msg.emit(f"Frame Config Error: {e}")

    @QtCore.pyqtSlot(int, int, float, float, float, float, float, float, float, float)
    def send_left_config(
        self,
        use_ramp: int,
        use_pi: int,
        kp: float,
        ki: float,
        k1: float,
        k2: float,
        k3: float,
        k_aw: float,
        alpha: float,
        rps: float,
    ) -> None:
        self._send_motor_config(0, use_ramp, use_pi, kp, ki, k1, k2, k3, k_aw, alpha, rps)

    @QtCore.pyqtSlot(int, int, float, float, float, float, float, float, float, float)
    def send_right_config(
        self,
        use_ramp: int,
        use_pi: int,
        kp: float,
        ki: float,
        k1: float,
        k2: float,
        k3: float,
        k_aw: float,
        alpha: float,
        rps: float,
    ) -> None:
        self._send_motor_config(1, use_ramp, use_pi, kp, ki, k1, k2, k3, k_aw, alpha, rps)

    def _send_motor_config(
        self,
        motor_id: int,
        use_ramp: int,
        use_pi: int,
        kp: float,
        ki: float,
        k1: float,
        k2: float,
        k3: float,
        k_aw: float,
        alpha: float,
        rps: float,
    ) -> None:
        """Constructs and sends a PID configuration packet to the MCU."""
        packet = self.protocol.create_pid_packet(
            motor_id, use_ramp, use_pi, kp, ki, k1, k2, k3, k_aw, alpha, rps
        )
        self._write(packet)

    @QtCore.pyqtSlot(
        int,
        int,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
        int,
        int,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
    )
    def send_all_config(
        self,
        l_use_ramp: int,
        l_use_pi: int,
        l_kp: float,
        l_ki: float,
        l_k1: float,
        l_k2: float,
        l_k3: float,
        l_k_aw: float,
        l_alpha: float,
        l_rps: float,
        r_use_ramp: int,
        r_use_pi: int,
        r_kp: float,
        r_ki: float,
        r_k1: float,
        r_k2: float,
        r_k3: float,
        r_k_aw: float,
        r_alpha: float,
        r_rps: float,
    ) -> None:
        packet = self.protocol.create_pid_packet_all_motors(
            l_use_ramp,
            l_use_pi,
            l_kp,
            l_ki,
            l_k1,
            l_k2,
            l_k3,
            l_k_aw,
            l_alpha,
            l_rps,
            r_use_ramp,
            r_use_pi,
            r_kp,
            r_ki,
            r_k1,
            r_k2,
            r_k3,
            r_k_aw,
            r_alpha,
            r_rps,
        )
        self._write(packet)
