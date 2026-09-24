"""
Background engine module for telemetry data processing.

The engine runs in its own QThread and orchestrates:
1. Input: a `Transport` drained by a dedicated `ReaderThread` (blocking reads, P5). The
   `VIRTUAL` port is a `SimTransport`: simulated protocol bytes through the same path (R2.7).
2. Protocol: parsing and decoding. This happens on the reader thread under `_data_lock`.
   Every configured stream is decoded (`FrameParser` + `StreamRouter`, R2.2/R2.3); which one
   the GUI shows is a view choice and never restarts acquisition.
3. Storage: one `SampleStore` per stream (`StreamStores`). The GUI pulls from them at its
   own frame rate (ADR-0002, R2.6), so the engine never pushes bulk data through Qt.
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

from core.acquisition.storage import StreamStores
from core.protocol.frame_parser import FrameParser
from core.protocol.handler import ProtocolHandler
from core.protocol.router import StreamRouter
from core.protocol.stats import LinkStats, make_link_report
from core.transport import (
    SIM_PORT_NAME,
    ReaderThread,
    SerialTransport,
    SimTransport,
    Transport,
    TransportError,
)
from core.types import EngineState, StreamConfig

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
    # The set of streams (and so the StreamStores' stores) changed; re-look-up stores.
    streams_configured = QtCore.pyqtSignal()

    def __init__(self, max_samples: int, stores: StreamStores | None = None) -> None:
        super().__init__()

        # Shared with the GUI, which reads snapshots from them directly (thread-safe).
        self.stores: StreamStores = stores if stores is not None else StreamStores(max_samples)
        # Parser and router run on the reader thread under `_data_lock`.
        self.parser = FrameParser()
        self.router = StreamRouter(self.parser.stats)
        self._encoder = ProtocolHandler()  # command packets only
        self._streams: dict[str, StreamConfig] = {}
        self._active_key: str | None = None  # the stream the simulator produces

        # --- IO & State ---
        # Creates the transport for a port name; tests swap in fakes.
        self.transport_factory: Callable[[str, int], Transport] = SerialTransport
        self.sim_factory: Callable[[StreamConfig], SimTransport] = SimTransport
        self._sim: SimTransport | None = None
        self._transport: Transport | None = None
        self._reader: ReaderThread | None = None
        self._data_lock = threading.Lock()
        self._reader_failed.connect(self._on_reader_failed)
        self.state: EngineState = EngineState.IDLE

        # --- Link statistics (C13) ---
        self.stats_timer: QtCore.QTimer = QtCore.QTimer(self)
        self.stats_timer.timeout.connect(self._emit_link_stats)
        self.stats_timer.setInterval(1000)
        self._stats_prev: LinkStats = LinkStats()
        self._stats_prev_samples: int = 0
        self._stats_prev_ts: float = 0.0
        self._time_resets_seen: dict[str, int] = {}

    @QtCore.pyqtSlot(str, int)
    def start_working(self, port_name: str, baudrate: int) -> None:
        """Initiates the data acquisition process."""
        if self.state == EngineState.RUNNING:
            self.status_msg.emit("Already running")
            return
        if self.state != EngineState.CONFIGURED:
            self.status_msg.emit("No stream configured yet")
            return
        if port_name == SIM_PORT_NAME and not self._streams:
            self.status_msg.emit("No valid stream to simulate")
            return

        # Clear buffers to prevent "time travel" artifacts
        self.stores.clear()
        with self._data_lock:
            self.parser.reset()
            self.router.stats = self.parser.stats
            self.router.reset_counters()
            self._stats_prev = self.parser.stats.snapshot()
            self._stats_prev_samples = self.stores.total_stored
        self._stats_prev_ts = time.monotonic()
        self._time_resets_seen = {}

        transport: Transport
        if port_name == SIM_PORT_NAME:
            transport = self._sim = self.sim_factory(self._sim_stream())
        else:
            transport = self.transport_factory(port_name, baudrate)
        try:
            transport.open()
        except TransportError as e:
            self._sim = None
            msg = f"Connection Error: {e}"
            self.status_msg.emit(msg)
            self.connection_failed.emit(msg)
            return
        self._transport = transport

        self._set_state(EngineState.RUNNING)
        self.stats_timer.start()
        self.status_msg.emit(f"Connected to {port_name}")
        # Start reading only once RUNNING, so an immediate failure is never ignored.
        self._reader = ReaderThread(transport, self._on_bytes, self._reader_failed.emit)
        self._reader.start()

    @QtCore.pyqtSlot()
    def stop_working(self) -> None:
        """Safely stops all operations."""
        self.stats_timer.stop()

        if self.state != EngineState.RUNNING:
            return

        self._set_state(EngineState.CONFIGURED)
        self._close_transport()

    def _close_transport(self) -> None:
        self._sim = None
        reader, self._reader = self._reader, None
        transport, self._transport = self._transport, None
        if reader is not None:
            reader.stop()
        if transport is not None:
            transport.close()

    @QtCore.pyqtSlot(dict)
    def configure_streams(self, streams: dict[str, StreamConfig]) -> None:
        """
        Sets every stream to decode (normally all valid streams in streams.json).

        This is safe while running: the router and stores are swapped atomically, and
        acquisition continues with the new definitions.
        """
        try:
            with self._data_lock:
                self.router.configure(streams)
        except (KeyError, ValueError) as e:
            self.status_msg.emit(f"Stream config error: {e}")
            return
        self.stores.configure(streams)
        self._streams = dict(streams)
        self._apply_active_stream()
        if self.state == EngineState.IDLE:
            self._set_state(EngineState.CONFIGURED)
        self.streams_configured.emit()

    @QtCore.pyqtSlot(str)
    def select_stream(self, key: str) -> None:
        """
        Marks the stream the GUI shows. Serial acquisition decodes every stream anyway, so
        this only retargets the simulator, and never restarts anything.
        """
        self._active_key = key  # may precede configure_streams() on a config reload
        self._apply_active_stream()

    def _sim_stream(self) -> StreamConfig:
        """The stream the simulator produces: the shown one, else the first configured."""
        key = self._active_key if self._active_key in self._streams else None
        return self._streams[key or next(iter(self._streams))]

    def _apply_active_stream(self) -> None:
        if self._sim is not None and self._streams:
            self._sim.set_stream(self._sim_stream())

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
            batches = self.router.route(self.parser.feed(data))
        for key, records in batches.items():
            store = self.stores.get(key)
            if store is not None:
                store.append_records(records)

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
            cur = self.parser.stats.snapshot()
        samples = self.stores.total_stored
        report = make_link_report(
            self._stats_prev, cur, samples - self._stats_prev_samples, now - self._stats_prev_ts
        )
        self._stats_prev, self._stats_prev_samples, self._stats_prev_ts = cur, samples, now
        self.link_stats.emit(report)
        self._report_time_resets()

    def _report_time_resets(self) -> None:
        """Says when a stream's time field went backwards: the device most likely restarted."""
        names = []
        for key in self.stores.keys():
            store = self.stores.get(key)
            if store is None:
                continue
            resets = store.time_resets
            if resets > self._time_resets_seen.get(key, 0):
                names.append(self._streams.get(key, {}).get("name", key))
            self._time_resets_seen[key] = resets
        if names:
            self.status_msg.emit(
                f"Time counter went backwards in {', '.join(names)} (device reset?); "
                "continuing on a new segment"
            )

    @QtCore.pyqtSlot(int)
    def set_capacity(self, max_samples: int) -> None:
        """Resizes every stream's buffer, keeping the newest samples."""
        self.stores.resize(max_samples)

    @QtCore.pyqtSlot(str, float)
    def set_time_scale(self, key: str, scale_s: float) -> None:
        """
        Overrides a stream's seconds per tick (the dashboard's per-stream Period). The whole
        history is re-timed, since the scale is applied when the GUI reads a snapshot.
        """
        store = self.stores.get(key)
        if store is not None:
            store.set_time_scale(scale_s)

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
        packet = self._encoder.create_pid_packet(
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
        packet = self._encoder.create_pid_packet_all_motors(
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
