"""
Background engine module for telemetry data processing.

The engine runs in its own QThread and orchestrates:
1. Input: a `Transport` drained by a dedicated `ReaderThread` (blocking reads, P5). The
   `VIRTUAL` port is a `SimTransport`: simulated protocol bytes through the same path (R2.7).
2. Protocol: decoding bytes into records, by a `LinkDecoder` (R8.1; binary frames are
   `FrameParser` + `StreamRouter`, R2.2/R2.3). This happens on the reader thread under
   `_data_lock`. Every configured stream is decoded; which one the GUI shows is a view
   choice and never restarts acquisition.
3. Storage: one `SampleStore` per stream (`StreamStores`). The GUI pulls from them at its
   own frame rate (ADR-0002, R2.6), so the engine never pushes bulk data through Qt.
4. GUI output: state, status and link statistics (small signals only).

Threads: the reader thread uses the decoder under `_data_lock`, and the engine thread
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
from core.protocol.commands import CommandDef
from core.protocol.link import BINARY, LinkDecoder, make_link_decoder
from core.protocol.stats import LinkStats, make_link_report
from core.recording.sbtp import RecordingError, RecordingWriter
from core.transport import (
    SIM_PORT_NAME,
    ReaderThread,
    SerialTransport,
    SimTransport,
    Transport,
    TransportError,
)
from core.transport.replay_transport import ReplayTransport
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
    # A session ended normally (a replay reached its end). Errors use connection_failed.
    session_ended = QtCore.pyqtSignal(str)
    # The file being recorded to (R4.1), or "" when not recording.
    recording_changed = QtCore.pyqtSignal(str)
    # A recording write failed on the reader thread; handled on the engine thread.
    _reader_failed_recording = QtCore.pyqtSignal(str)

    def __init__(self, max_samples: int, stores: StreamStores | None = None) -> None:
        super().__init__()

        # Shared with the GUI, which reads snapshots from them directly (thread-safe).
        self.stores: StreamStores = stores if stores is not None else StreamStores(max_samples)
        # The decoder runs on the reader thread under `_data_lock`. Its format is the
        # profile's (R8.2), except during a replay of a recording made with another one.
        self.link: LinkDecoder = make_link_decoder()
        self._link_format = BINARY
        self._profile: dict[str, str] = {"name": "", "format": BINARY}
        self._commands: tuple[CommandDef, ...] = ()  # for the simulator (R5.2)
        self._streams: dict[str, StreamConfig] = {}
        self._active_key: str | None = None  # the stream the simulator produces

        # --- IO & State ---
        # Creates the transport for a port name; tests swap in fakes.
        self.transport_factory: Callable[[str, int], Transport] = SerialTransport
        self.sim_factory: Callable[[StreamConfig], SimTransport] = SimTransport
        self._sim: SimTransport | None = None
        self._replay: ReplayTransport | None = None
        # Raw recording (R4.1): written on the reader thread, swapped on the engine thread.
        self._recorder: RecordingWriter | None = None
        self._rec_lock = threading.Lock()
        self._transport: Transport | None = None
        self._reader: ReaderThread | None = None
        self._data_lock = threading.Lock()
        self._reader_failed.connect(self._on_reader_failed)
        self._reader_failed_recording.connect(self._on_recording_failed)
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
        """Connects to a serial port, or to the simulator (`VIRTUAL`)."""
        if not self._can_start():
            return
        if not self._use_format(self._profile["format"]):
            return
        if port_name == SIM_PORT_NAME:
            if not self._streams:
                self.status_msg.emit("No valid stream to simulate")
                return
            self._sim = self.sim_factory(self._sim_stream())
            self._sim.set_commands(self._commands)
            if not self._start(self._sim, port_name):
                self._sim = None
        else:
            self._start(self.transport_factory(port_name, baudrate), port_name)

    @QtCore.pyqtSlot(str, float)
    def start_replay(self, path: str, speed: float) -> None:
        """Plays a recording through the pipeline (R4.2); `speed` 0 means as fast as possible."""
        if not self._can_start():
            return
        try:
            replay = ReplayTransport(path, speed if speed > 0 else None)
        except RecordingError as e:
            msg = f"Cannot replay: {e}"
            self.status_msg.emit(msg)
            self.connection_failed.emit(msg)
            return
        recorded_format = replay.header.profile_format
        if not self._use_format(recorded_format):
            return
        # Set before the reader starts: a short replay can end before _start() returns.
        self._replay = replay
        if not self._start(replay, replay.name):
            self._replay = None
            return
        recorded = {k: v.get("frame") for k, v in replay.header.streams.items()}
        current = {k: v.get("frame") for k, v in self._streams.items()}
        if recorded_format != self._profile["format"]:
            self.status_msg.emit(
                f"Replaying {replay.path.name}: recorded as {recorded_format}, not this "
                f"profile's {self._profile['format']}; decoding as {recorded_format}"
            )
        elif recorded != current:
            self.status_msg.emit(
                f"Replaying {replay.path.name}: it was recorded with different frame "
                "layouts; decoding with the current streams.json"
            )

    @QtCore.pyqtSlot(str, str)
    def configure_profile(self, name: str, fmt: str) -> None:
        """
        The device profile in use (R8.2): its name goes into recordings, its wire format
        picks the decoder. Takes effect from the next connection; the GUI only switches
        profiles while disconnected.
        """
        self._profile = {"name": name, "format": fmt}
        if self.state != EngineState.RUNNING:
            self._use_format(fmt, connecting=False)

    def _use_format(self, fmt: str, connecting: bool = True) -> bool:
        """
        Swaps in a decoder for `fmt` (configured for the streams) if it isn't the one.
        A format no decoder reads is reported; when connecting, the connection fails.
        """
        if fmt == self._link_format:
            return True
        try:
            link = make_link_decoder(fmt)
        except ValueError as e:
            msg = f"Cannot decode: {e}"
            self.status_msg.emit(msg)
            if connecting:
                self.connection_failed.emit(msg)
            return False
        with self._data_lock:
            link.configure(self._streams)
            self.link = link
            self._link_format = fmt
        return True

    def _can_start(self) -> bool:
        if self.state == EngineState.RUNNING:
            self.status_msg.emit("Already running")
            return False
        if self.state != EngineState.CONFIGURED:
            self.status_msg.emit("No stream configured yet")
            return False
        return True

    def _start(self, transport: Transport, label: str) -> bool:
        """Common start: resets the session, opens the transport, starts reading."""
        # Clear buffers to prevent "time travel" artifacts
        self.stores.clear()
        with self._data_lock:
            self.link.reset()
            self._stats_prev = self.link.stats.snapshot()
            self._stats_prev_samples = self.stores.total_stored
        self._stats_prev_ts = time.monotonic()
        self._time_resets_seen = {}

        try:
            transport.open()
        except TransportError as e:
            msg = f"Connection Error: {e}"
            self.status_msg.emit(msg)
            self.connection_failed.emit(msg)
            return False
        self._transport = transport

        self._set_state(EngineState.RUNNING)
        self.stats_timer.start()
        self.status_msg.emit(f"Connected to {label}")
        # Start reading only once RUNNING, so an immediate failure is never ignored.
        self._reader = ReaderThread(transport, self._on_bytes, self._reader_failed.emit)
        self._reader.start()
        return True

    @QtCore.pyqtSlot()
    def stop_working(self) -> None:
        """Safely stops all operations."""
        self.stats_timer.stop()

        if self.state != EngineState.RUNNING:
            return

        self._set_state(EngineState.CONFIGURED)
        self._close_transport()
        self.stop_recording()

    def _close_transport(self) -> None:
        self._sim = None
        self._replay = None
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

        This is safe while running: the decoder and stores are swapped atomically, and
        acquisition continues with the new definitions.
        """
        try:
            with self._data_lock:
                self.link.configure(streams)
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
        """Reader-thread callback: records (if on), parses one chunk and stores its frames."""
        with self._rec_lock:
            if self._recorder is not None:
                try:
                    self._recorder.write(data)
                except OSError as e:  # disk full, drive removed: stop recording, keep reading
                    logger.error("Recording failed: %s", e)
                    self._recorder = None
                    self._reader_failed_recording.emit(str(e))
        with self._data_lock:
            batches = self.link.feed(data)
        for key, records in batches.items():
            store = self.stores.get(key)
            if store is not None:
                store.append_records(records)

    @QtCore.pyqtSlot(str)
    def _on_reader_failed(self, message: str) -> None:
        """Engine-thread handler for a transport failure reported by the reader thread."""
        if self.state != EngineState.RUNNING:
            return
        replay = self._replay
        if replay is not None and replay.finished:
            note = ""
            if replay.truncated_bytes:
                note = f" ({replay.truncated_bytes} B at the end were cut off)"
            self.stop_working()
            self.status_msg.emit(f"Replay finished: {replay.path.name}{note}")
            self.session_ended.emit(f"Replay finished: {replay.path.name}")
            return
        msg = f"Serial error: {message}"
        self.status_msg.emit(msg)
        self.connection_failed.emit(msg)
        self.stop_working()

    def _emit_link_stats(self) -> None:
        """Periodic task (stats_timer): emits counters and rates since the previous report."""
        now = time.monotonic()
        with self._data_lock:
            cur = self.link.stats.snapshot()
        samples = self.stores.total_stored
        report = make_link_report(
            self._stats_prev, cur, samples - self._stats_prev_samples, now - self._stats_prev_ts
        )
        self._stats_prev, self._stats_prev_samples, self._stats_prev_ts = cur, samples, now
        self.link_stats.emit(report)
        self._report_time_resets()
        with self._rec_lock:
            recorder = self._recorder
        if recorder is not None:
            recorder.flush()  # a crash loses at most about a second of recording

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

    # --- recording and replay (R4.1, R4.2) ---------------------------------------------

    @QtCore.pyqtSlot(str)
    def start_recording(self, path: str) -> None:
        """Records every byte received from now on to a new `.sbtp` file."""
        if self.state != EngineState.RUNNING:
            self.status_msg.emit("Connect first: a recording starts with a session")
            return
        self.stop_recording()
        source = self._transport.name if self._transport is not None else ""
        try:
            recorder = RecordingWriter(
                path, dict(self._streams), source, profile=dict(self._profile)
            )
        except OSError as e:
            self.status_msg.emit(f"Cannot record: {e}")
            self.recording_changed.emit("")
            return
        with self._rec_lock:
            self._recorder = recorder
        self.status_msg.emit(f"Recording to {recorder.path}")
        self.recording_changed.emit(str(recorder.path))

    @QtCore.pyqtSlot()
    def stop_recording(self) -> None:
        with self._rec_lock:
            recorder, self._recorder = self._recorder, None
        if recorder is None:
            return
        recorder.close()
        self.status_msg.emit(
            f"Recording saved: {recorder.path.name} ({recorder.bytes / 1000:.1f} kB)"
        )
        self.recording_changed.emit("")

    @QtCore.pyqtSlot(str)
    def _on_recording_failed(self, message: str) -> None:
        self.status_msg.emit(f"Recording stopped: {message}")
        self.recording_changed.emit("")

    @QtCore.pyqtSlot(float)
    def set_replay_speed(self, speed: float) -> None:
        """0 means as fast as possible."""
        if self._replay is not None:
            self._replay.set_speed(speed if speed > 0 else None)

    @QtCore.pyqtSlot(bool)
    def set_replay_paused(self, paused: bool) -> None:
        if self._replay is not None:
            self._replay.set_paused(paused)

    @QtCore.pyqtSlot()
    def replay_step(self) -> None:
        """While a replay is paused: plays one more recorded read."""
        if self._replay is not None:
            self._replay.step()

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

    @QtCore.pyqtSlot(object)
    def configure_commands(self, commands: tuple[CommandDef, ...]) -> None:
        """The document's command layouts, so the simulator can parse what it's sent (R5.2)."""
        self._commands = tuple(commands)
        if self._sim is not None:
            self._sim.set_commands(self._commands)

    @QtCore.pyqtSlot(bytes)
    def send_packet(self, packet: bytes) -> None:
        """
        Writes one command packet, encoded by the GUI from a config-defined command (R5.2).
        Only complete packets travel through the queue, never partial writes.
        """
        self._write(packet)
