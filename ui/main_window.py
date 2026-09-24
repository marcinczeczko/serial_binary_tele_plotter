"""
Main Application Window Module.

This module defines the primary GUI window that integrates the control panel,
plotting area, and the background telemetry engine. It acts as the **Controller**
in the application architecture, managing high-level signal wiring, thread
lifecycle, and global events.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PyQt6 import QtCore, QtGui, QtWidgets

from core.acquisition.engine import TelemetryEngine
from core.acquisition.storage import StreamStores
from core.acquisition.timebase import time_base_config
from core.analysis.export import ExportError, export_table, parquet_available
from core.analysis.step_response import StepMetrics, step_metrics
from core.analysis.trigger import TriggerSpec
from core.config import DEFAULT_CONFIG_PATH, SCHEMA_VERSION, StreamConfigLoader
from core.protocol.commands import CommandError, encode_command, resolve_values
from core.protocol.stats import LinkReport, format_link_report
from core.recording.sbtp import SUFFIX as RECORDING_SUFFIX
from core.recording.sbtp import recording_name
from core.types import EngineState, PlotMode, PlotPacketWithBounds, StreamConfig
from ui.app_settings import (
    DEFAULT_RECORDINGS_DIR,
    KEY_RECORD_ON_CONNECT,
    KEY_RECORDINGS_DIR,
    app_settings,
)
from ui.charts.live_feed import LiveFeed
from ui.charts.telemetry_plot import TelemetryPlot
from ui.charts.trigger_controller import TriggerController
from ui.config.tab import ConfiguratorTab
from ui.panels.command_panel import SendRequest
from ui.panels.container import MainControlPanel
from ui.ui_state import UiState

logger = logging.getLogger(__name__)


def _action(
    menu: QtWidgets.QMenu,
    text: str,
    slot: Callable[..., Any],
    checkable: bool = False,
    on_toggle: bool | None = None,
) -> QtGui.QAction:
    """A menu action; checkable ones call `slot(checked)` on every toggle unless told not to."""
    act = menu.addAction(text)
    assert act is not None
    act.setCheckable(checkable)
    (act.toggled if (checkable if on_toggle is None else on_toggle) else act.triggered).connect(
        slot
    )
    return act


class MainWindow(QtWidgets.QMainWindow):
    """
    The main window of the Serial Binary Plotter.

    This class serves as the central hub of the application. Its responsibilities include:
    1. **Composition**: Instantiating the UI components (ControlPanel, PlotArea).
    2. **Threading**: Setting up the background engine thread (`TelemetryEngine`).
    3. **Wiring**: Connecting signals/slots between the UI (Main Thread) and
       the Engine (Worker Thread).
    4. **Lifecycle**: Managing startup configuration and safe shutdown sequences.
    """

    # To the engine thread (queued): an encoded command packet, and the command layouts.
    send_packet = QtCore.pyqtSignal(bytes)
    commands_configured = QtCore.pyqtSignal(object)

    def __init__(
        self,
        config_path: Path = DEFAULT_CONFIG_PATH,
        settings: QtCore.QSettings | None = None,
    ) -> None:
        """
        Initializes the main window, UI layout, and background engine.

        `config_path` is the streams.json to use; one loader for it is shared by the
        dashboard and the Configuration tab. `settings` holds what's remembered between
        runs (tests pass a private one).
        """
        super().__init__()
        self.settings = settings if settings is not None else app_settings()
        self.stream_loader = StreamConfigLoader(config_path)
        self.ui_state = UiState(self.settings, self.stream_loader.path)

        # --- State Tracking ---
        # Mirror of the engine's state, updated only from `state_changed` (never read across
        # threads).
        self.engine_state: EngineState = EngineState.IDLE
        self._shut_down = False
        # Per-stream Period overrides for this session (seconds per tick), R2.5.
        self._scale_overrides: dict[str, float] = {}
        self._session_label = ""  # port or replay being run ("" when idle)
        self._replaying = False
        self._recording_path = ""
        # Trigger captures (R4.4/R4.5): the previous one, for metrics and the overlay.
        self._last_capture: tuple[PlotPacketWithBounds, float] | None = None
        self._last_metrics: StepMetrics | None = None

        # --- Window Setup ---
        self.setWindowTitle("Serial Binary Plotter")
        self.resize(1280, 800)

        # --- MAIN LAYOUT (TABS) ---
        # Top-level tabs: dashboard (panel + plot) and configuration editor
        self.tabs = QtWidgets.QTabWidget()
        self.setCentralWidget(self.tabs)

        # ================= TAB 1: DASHBOARD =================
        self.dashboard_widget = QtWidgets.QWidget()
        dashboard_layout = QtWidgets.QVBoxLayout(self.dashboard_widget)
        dashboard_layout.setContentsMargins(0, 0, 0, 0)

        # Splitter (control panel + plot)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)

        # Instantiate the main view components
        self.panel = MainControlPanel(self.stream_loader, self.ui_state)
        remembered = self.ui_state.connection()
        if remembered is not None:
            self.panel.conn_panel.select(*remembered)
        self.plot = TelemetryPlot()

        self.splitter.addWidget(self.panel)
        self.splitter.addWidget(self.plot)
        self.splitter.setSizes([350, 930])

        # Put the splitter into the dashboard tab
        dashboard_layout.addWidget(self.splitter)

        # Register the dashboard tab
        self.tabs.addTab(self.dashboard_widget, "📊 Dashboard")

        # ================= TAB 2: CONFIGURATION =================
        self.configurator = ConfiguratorTab(self.stream_loader)
        self.configurator.config_saved.connect(self._reload_configuration)
        self.tabs.addTab(self.configurator, "⚙️ Configuration")

        # --- Status Bar Initialization ---
        self.status_bar = QtWidgets.QStatusBar()
        self.setStatusBar(self.status_bar)

        self.lbl_status = QtWidgets.QLabel("Ready")
        self.lbl_cursor = QtWidgets.QLabel("")
        self.lbl_link = QtWidgets.QLabel("")
        self.lbl_rec = QtWidgets.QLabel("")
        self.lbl_rec.setStyleSheet("color: #F44336; font-weight: bold;")

        self.status_bar.addWidget(self.lbl_status)
        self.status_bar.addPermanentWidget(self.lbl_rec)
        self.status_bar.addPermanentWidget(self.lbl_link)
        self.status_bar.addPermanentWidget(self.lbl_cursor)

        # --- Engine & Thread Initialization ---
        # Retrieve initial settings via the Panel's public API
        initial_samples = self.panel.get_initial_sample_count()

        # One store per stream, shared: the engine's reader thread writes them, and the GUI
        # pulls from the store of the stream it shows.
        self.stores = StreamStores(initial_samples)
        self.engine: TelemetryEngine = TelemetryEngine(initial_samples, stores=self.stores)
        self.live_feed = LiveFeed(None, self.plot, parent=self)
        self.trigger = TriggerController(self)

        self.engine_thread: QtCore.QThread = QtCore.QThread(self)
        self.engine.moveToThread(self.engine_thread)
        self.engine_thread.finished.connect(self.engine.deleteLater)
        self.engine_thread.finished.connect(self.engine_thread.deleteLater)
        self.engine_thread.start()

        # --- Signal Wiring ---

        # 1. Configuration: Panel -> Engine
        self.panel.stream_changed.connect(self._on_stream_changed)
        self.panel.period_changed.connect(self._on_period_changed)
        self.panel.samples_changed.connect(self.engine.set_capacity)
        self.panel.send_requested.connect(self._send_command)
        self.send_packet.connect(self.engine.send_packet)
        self.commands_configured.connect(self.engine.configure_commands)

        # 2. Control Logic: Panel -> Main Window
        self.panel.connection_requested.connect(self._handle_connection)
        self.panel.pause_requested.connect(self._handle_pause)

        # 3. Visuals: Panel -> Plot
        self.panel.signal_visibility_changed.connect(self.plot.set_signal_visible)
        self.panel.signal_visibility_changed.connect(lambda *_: self.live_feed.invalidate())
        self.panel.signal_lane_changed.connect(self.plot.move_signal)

        # 4. Engine -> UI (small signals only; plot data is pulled by LiveFeed)
        self.engine.status_msg.connect(self.lbl_status.setText)
        self.engine.connection_failed.connect(self._handle_connection_failed)
        self.engine.state_changed.connect(self._on_engine_state_changed)
        self.engine.streams_configured.connect(self._bind_live_feed)
        self.engine.link_stats.connect(self._on_link_stats)
        self.engine.session_ended.connect(self._on_session_ended)
        self.engine.recording_changed.connect(self._on_recording_changed)

        # 5. Trigger capture and step response (R4.4, R4.5)
        trigger_panel = self.panel.trigger_panel
        trigger_panel.arm_requested.connect(self._arm_trigger)
        trigger_panel.disarm_requested.connect(self.trigger.disarm)
        self.trigger.state_changed.connect(trigger_panel.show_state)
        self.trigger.captured.connect(self._on_trigger_captured)

        # 6. Interactivity: Plot -> UI
        self.plot.cursor_moved.connect(self.lbl_cursor.setText)

        self._build_menus()

        # --- Final Setup ---
        self._configure_engine_streams()
        self._initial_stream_setup()
        self._report_config_problems()

    def _reload_configuration(self) -> None:
        """Re-reads streams.json after the Configuration tab saved it, keeping the selection."""
        try:
            self.panel.reload_streams()
        except ValueError as e:
            self.lbl_status.setText(f"Could not reload streams.json: {e}")
            self.lbl_status.setStyleSheet("color: #F44336; font-weight: bold;")
            return
        self._configure_engine_streams()
        self.lbl_status.setText("Configuration reloaded from disk.")
        self.lbl_status.setToolTip("")
        self.lbl_status.setStyleSheet("")
        self._report_config_problems()

    def _report_config_problems(self) -> None:
        """
        Surfaces streams.json problems, and a migration from an older schema, in the status
        bar (no modal dialog at startup).
        """
        loader = self.panel.stream_loader
        problems = loader.problems
        for problem in problems:
            logger.warning("streams.json: %s", problem)
        for note in loader.migration_notes:
            logger.info("streams.json: %s", note)
        details = [str(p) for p in problems]
        parts: list[str] = []
        if problems:
            errors = [p for p in problems if p.severity == "error"]
            text = f"{len(problems)} problem(s)"
            if errors:
                left_out = {p.stream or p.item for p in errors}
                text += f", {len(left_out)} stream(s)/command(s)/panel(s) not loaded"
            parts.append(text)
        if loader.migrated:
            parts.append(
                f"schema {loader.source_version} read as {SCHEMA_VERSION}; save it from the "
                "Configuration tab to update the file"
            )
            details += loader.migration_notes
        if not parts:
            return
        self.lbl_status.setText("streams.json: " + "; ".join(parts) + " (hover for details)")
        self.lbl_status.setToolTip("\n".join(details))
        self.lbl_status.setStyleSheet("color: #FFB74D; font-weight: bold;")

    def _configure_engine_streams(self) -> None:
        """Tells the engine to decode every valid stream (A1); the GUI picks one to show."""
        # Fresh stores use the (possibly just edited) streams.json time bases.
        self._scale_overrides.clear()
        self._show_period()
        self.commands_configured.emit(tuple(self.stream_loader.commands.values()))
        QtCore.QMetaObject.invokeMethod(
            self.engine,
            "configure_streams",
            QtCore.Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(dict, dict(self.stream_loader.list_streams())),
        )

    def _bind_live_feed(self) -> None:
        """Points the live feed at the shown stream's store (after stores are (re)built)."""
        store = self.stores.get(self.panel.current_stream_key())
        self.live_feed.set_store(store)
        self.trigger.set_store(store)

    def _initial_stream_setup(self) -> None:
        """Applies the stream currently selected in the panel to the plot and the engine."""
        cfg = self.panel.get_current_stream_config()
        if cfg:
            self._on_stream_changed(cfg)

    def _on_stream_changed(self, stream_cfg: StreamConfig) -> None:
        """
        Shows another stream. All streams are decoded all the time, so this is only a view
        change: acquisition isn't restarted and the stream's history is kept. The engine
        is told only so the virtual device simulates the shown stream.
        """
        self.plot.configure_stream(stream_cfg)
        self._bind_live_feed()
        self._show_period()
        key = self.panel.current_stream_key()
        if key is not None:
            QtCore.QMetaObject.invokeMethod(
                self.engine,
                "select_stream",
                QtCore.Qt.ConnectionType.QueuedConnection,
                QtCore.Q_ARG(str, key),
            )

    def _show_period(self) -> None:
        """Shows the shown stream's period: its session override, else streams.json."""
        cfg = self.panel.get_current_stream_config()
        key = self.panel.current_stream_key()
        if cfg is None or key is None:
            return
        time_cfg = time_base_config(cfg)
        scale_s = self._scale_overrides.get(key, time_cfg.scale_s)
        self.panel.time_panel.show_period(
            scale_s * time_cfg.step * 1000.0, time_cfg.period_s * 1000.0
        )

    def _on_period_changed(self, period_ms: float) -> None:
        """The user overrode the shown stream's period: re-time that stream (all history)."""
        cfg = self.panel.get_current_stream_config()
        key = self.panel.current_stream_key()
        if cfg is None or key is None or period_ms <= 0:
            return
        scale_s = period_ms / 1000.0 / time_base_config(cfg).step
        self._scale_overrides[key] = scale_s
        QtCore.QMetaObject.invokeMethod(
            self.engine,
            "set_time_scale",
            QtCore.Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(str, key),
            QtCore.Q_ARG(float, scale_s),
        )

    def _handle_connection(self, port: str, baud: int) -> None:
        """
        Handles connection requests triggered by the Control Panel.
        """
        if port != "STOP":
            self.ui_state.set_connection(port, baud)
            self._session_label = port
            self._replaying = False
            QtCore.QMetaObject.invokeMethod(
                self.engine,
                "start_working",
                QtCore.Qt.ConnectionType.QueuedConnection,
                QtCore.Q_ARG(str, port),
                QtCore.Q_ARG(int, baud),
            )
            # "Connected" is shown only once the engine reports RUNNING (C12).
            self.lbl_status.setText(f"Connecting to {port}...")
            self.lbl_status.setStyleSheet("")
        else:
            QtCore.QMetaObject.invokeMethod(
                self.engine, "stop_working", QtCore.Qt.ConnectionType.QueuedConnection
            )
            self._set_pause_state(False, update_status=False)
            self.lbl_status.setText("Disconnected")
            self.lbl_status.setStyleSheet("color: #F44336; font-weight: bold;")

    def _on_engine_state_changed(self, state: EngineState) -> None:
        self.engine_state = state
        running = state == EngineState.RUNNING
        # The Connect button mirrors the engine, however the session started (menu replay).
        self.panel.conn_panel.set_connected(running)
        self._update_menus()
        if running:
            self.lbl_status.setStyleSheet("color: #4CAF50; font-weight: bold;")
            if self.act_record_on_connect.isChecked() and not self._replaying:
                self._start_recording()
        else:
            self._replaying = False
            self._update_menus()

    def _on_session_ended(self, message: str) -> None:
        """A replay reached its end: the data stays for analysis."""
        self.lbl_status.setText(message)
        self.lbl_status.setStyleSheet("")

    def _handle_connection_failed(self, message: str) -> None:
        """
        Syncs UI state after a connection failure from the worker.
        """
        self.panel.conn_panel.set_connected(False)
        self._set_pause_state(False, update_status=False)
        self.lbl_status.setText(message)
        self.lbl_status.setStyleSheet("color: #F44336; font-weight: bold;")

    def _on_link_stats(self, report: LinkReport) -> None:
        text, tooltip, has_problems = format_link_report(report)
        self.lbl_link.setText(text)
        self.lbl_link.setToolTip(tooltip)
        self.lbl_link.setStyleSheet("color: #FFB74D;" if has_problems else "")

    def _handle_pause(self, paused: bool) -> None:
        """
        Toggles the pause state of the plotter.
        """
        self._set_pause_state(paused, update_status=True)

    def _set_pause_state(self, paused: bool, update_status: bool) -> None:
        # Pausing freezes a copy of *all* signals; acquisition keeps running underneath.
        self.panel.conn_panel.set_paused(paused)  # when not from the button itself
        self.plot.set_paused(paused, self.live_feed.freeze() if paused else None)
        if not paused:
            self.live_feed.invalidate()
        if update_status:
            self.lbl_status.setText("PAUSED" if paused else "Connected")

    # --- commands (R5.2) -----------------------------------------------------------------

    def _send_command(self, request: SendRequest) -> None:
        """Encodes a panel button's command and hands the packet to the engine."""
        command = self.stream_loader.commands.get(request.button.command)
        if command is None:
            return  # a panel only offers valid commands; kept for reloads in flight
        if self.engine_state != EngineState.RUNNING:
            self._command_status(f"Not connected: '{request.button.label}' not sent", error=True)
            return
        try:
            values = resolve_values(command, request.params, request.column, request.button.values)
            packet = encode_command(command, values)
        except CommandError as e:
            self._command_status(f"Not sent: {e}", error=True)
            return
        self.send_packet.emit(packet)
        self._command_status(
            f"Sent '{request.button.label}': {command.label} "
            f"(ID 0x{command.packet_id:02X}, {len(packet)} B)"
        )

    def _command_status(self, text: str, error: bool = False) -> None:
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet("color: #F44336; font-weight: bold;" if error else "")

    # --- menus: recording, replay, export (R4.1-R4.3) -----------------------------------

    def _build_menus(self) -> None:
        bar = self.menuBar()
        assert bar is not None
        file_menu = bar.addMenu("&File")
        view_menu = bar.addMenu("&View")
        rec_menu = bar.addMenu("&Recording")
        assert file_menu is not None and view_menu is not None and rec_menu is not None
        self.act_reset_view = _action(
            view_menu, "Reset view to streams.json", self.panel.reset_view
        )

        self.act_export_shown = _action(file_menu, "Export shown stream…", self._export_shown)
        self.act_export_all = _action(file_menu, "Export all streams…", self._export_all)

        self.act_record = _action(rec_menu, "Record", self._toggle_recording, checkable=True)
        self.act_record.setShortcut(QtGui.QKeySequence("Ctrl+R"))
        self.act_record_on_connect = _action(
            rec_menu, "Record automatically on connect", self._save_record_on_connect, True
        )
        self.act_record_on_connect.setChecked(
            self.settings.value(KEY_RECORD_ON_CONNECT, False, type=bool)
        )
        _action(rec_menu, "Recordings folder…", self._choose_recordings_dir)
        rec_menu.addSeparator()
        self.act_replay = _action(rec_menu, "Replay a recording…", self._choose_replay)
        speed_menu = rec_menu.addMenu("Replay speed")
        assert speed_menu is not None
        self.speed_group = QtGui.QActionGroup(speed_menu)
        self.speed_actions: dict[float, QtGui.QAction] = {}
        for speed, label in ((1.0, "1×"), (2.0, "2×"), (5.0, "5×"), (10.0, "10×"), (0.0, "Max")):
            # triggered, not toggled: in an exclusive group the old choice toggles off too
            act = _action(
                speed_menu, label, lambda _=False, v=speed: self._set_speed(v), True, False
            )
            self.speed_group.addAction(act)
            self.speed_actions[speed] = act
        self.speed_actions[1.0].setChecked(True)
        self.act_replay_pause = _action(
            rec_menu, "Pause replay", self._toggle_replay_pause, checkable=True
        )
        self.act_replay_step = _action(rec_menu, "Step replay (one read)", self._replay_step)
        self._update_menus()

    def _update_menus(self) -> None:
        running = self.engine_state == EngineState.RUNNING
        self.act_record.setEnabled(running or bool(self._recording_path))
        self.act_replay_pause.setEnabled(running and self._replaying)
        self.act_replay_step.setEnabled(running and self._replaying)
        if not (running and self._replaying):
            self.act_replay_pause.setChecked(False)

    def _invoke(self, method: str, *args: QtCore.QGenericArgument) -> None:
        QtCore.QMetaObject.invokeMethod(
            self.engine, method, QtCore.Qt.ConnectionType.QueuedConnection, *args
        )

    def recordings_dir(self) -> Path:
        stored = self.settings.value(KEY_RECORDINGS_DIR, "", type=str)
        return Path(stored) if stored else DEFAULT_RECORDINGS_DIR

    def _start_recording(self) -> None:
        label = self.panel.current_stream_key() or self._session_label or "recording"
        path = self.recordings_dir() / recording_name(label)
        self._invoke("start_recording", QtCore.Q_ARG(str, str(path)))

    def _toggle_recording(self, checked: bool) -> None:
        if checked:
            self._start_recording()
        else:
            self._invoke("stop_recording")

    def _on_recording_changed(self, path: str) -> None:
        self._recording_path = path
        self.act_record.blockSignals(True)
        self.act_record.setChecked(bool(path))
        self.act_record.blockSignals(False)
        self.lbl_rec.setText(f"● REC {Path(path).name}" if path else "")
        self.lbl_rec.setToolTip(path)
        self._update_menus()

    def _save_record_on_connect(self, checked: bool) -> None:
        self.settings.setValue(KEY_RECORD_ON_CONNECT, checked)

    def _choose_recordings_dir(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Recordings folder", str(self.recordings_dir())
        )
        if folder:
            self.settings.setValue(KEY_RECORDINGS_DIR, folder)

    def _choose_replay(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Replay a recording",
            str(self.recordings_dir()),
            f"Recordings (*{RECORDING_SUFFIX});;All files (*)",
        )
        if path:
            self.start_replay(path)

    def start_replay(self, path: str) -> None:
        """Stops any session and plays `path` through the pipeline at the chosen speed."""
        speed = next((v for v, a in self.speed_actions.items() if a.isChecked()), 1.0)
        self._set_pause_state(False, update_status=False)
        self._session_label = Path(path).name
        self._replaying = True
        self._invoke("stop_working")
        self._invoke("start_replay", QtCore.Q_ARG(str, path), QtCore.Q_ARG(float, speed))
        self.lbl_status.setText(f"Replaying {Path(path).name}…")
        self.lbl_status.setStyleSheet("")

    def _set_speed(self, speed: float) -> None:
        self._invoke("set_replay_speed", QtCore.Q_ARG(float, speed))

    def _toggle_replay_pause(self, paused: bool) -> None:
        self._invoke("set_replay_paused", QtCore.Q_ARG(bool, paused))

    def _replay_step(self) -> None:
        self._invoke("replay_step")

    def _export_packet(self) -> tuple[PlotPacketWithBounds | None, tuple[float, float] | None]:
        """The shown stream's data to export: the paused view's time range, else the window."""
        if self.plot.mode == PlotMode.ANALYSIS and self.plot.analysis_packet is not None:
            (x0, x1), _ = self.plot.plot.viewRange()
            return self.plot.analysis_packet, (float(x0), float(x1))
        return self.live_feed.freeze(), None

    def _export_filter(self) -> str:
        filters = ["CSV (*.csv)"]
        if parquet_available():
            filters.append("Parquet (*.parquet)")
        return ";;".join(filters)

    def _ask_export_path(self, title: str, suggested: str) -> Path | None:
        path, chosen = QtWidgets.QFileDialog.getSaveFileName(
            self, title, str(self.recordings_dir() / suggested), self._export_filter()
        )
        if not path:
            return None
        target = Path(path)
        if target.suffix.lower() not in (".csv", ".parquet"):
            target = target.with_suffix(".parquet" if "parquet" in chosen.lower() else ".csv")
        return target

    def _export_shown(self) -> None:
        key = self.panel.current_stream_key() or "stream"
        target = self._ask_export_path("Export shown stream", f"{key}.csv")
        if target is not None:
            self.export_shown(target)

    def export_shown(self, target: Path) -> int:
        """Writes the shown stream (the paused view's range, or the window); returns rows."""
        packet, x_range = self._export_packet()
        if packet is None:
            self._report_export("Nothing to export yet")
            return 0
        try:
            rows = export_table(target, packet["time"], packet["signals"], x_range)
        except ExportError as e:
            self._report_export(f"Export failed: {e}")
            return 0
        scope = f"{x_range[0]:.3f}-{x_range[1]:.3f} s" if x_range else "the whole window"
        self._report_export(f"Exported {rows} rows ({scope}) to {target}")
        return rows

    def _export_all(self) -> None:
        target = self._ask_export_path("Export all streams (one file each)", "telemetry.csv")
        if target is not None:
            self.export_all(target)

    def export_all(self, target: Path) -> list[Path]:
        """Writes every stream's window to `<stem>_<stream><suffix>`; returns the files."""
        written: list[Path] = []
        for key in self.stores.keys():
            store = self.stores.get(key)
            snapshot = store.snapshot(None, None) if store is not None else None
            if snapshot is None:
                continue
            path = target.with_name(f"{target.stem}_{key}{target.suffix}")
            try:
                export_table(path, snapshot.time, snapshot.signals)
            except ExportError as e:
                self._report_export(f"Export failed: {e}")
                return written
            written.append(path)
        self._report_export(
            f"Exported {len(written)} stream(s) to {target.parent}"
            if written
            else "Nothing to export yet"
        )
        return written

    def _report_export(self, message: str) -> None:
        self.lbl_status.setText(message)
        self.lbl_status.setStyleSheet("")

    # --- trigger capture and step response (R4.4, R4.5) ---------------------------------

    def _arm_trigger(self, spec: TriggerSpec) -> None:
        if self.engine_state != EngineState.RUNNING:
            self.panel.trigger_panel.set_armed(False)
            self.lbl_status.setText("Connect first: the trigger watches live data")
            return
        if self.plot.mode == PlotMode.ANALYSIS:  # watch live data again
            self._set_pause_state(False, update_status=False)
        self.trigger.arm(spec)

    def _on_trigger_captured(self, packet: PlotPacketWithBounds, t_trig: float, note: str) -> None:
        """Freezes the capture for analysis, Δ anchored at the trigger, with metrics."""
        self.panel.conn_panel.set_paused(True)
        self.plot.set_paused(True, packet)
        t = packet["time"]
        if len(t):
            self.plot.plot.setXRange(float(t[0]), float(t[-1]), padding=0.02)
        self.plot.set_anchor(t_trig)

        panel = self.panel.trigger_panel
        metrics = None
        pair = panel.step_signals()
        if pair is not None and pair[0] in packet["signals"] and pair[1] in packet["signals"]:
            metrics = step_metrics(
                t, packet["signals"][pair[0]], packet["signals"][pair[1]], t_trig
            )
        panel.show_metrics(metrics, self._last_metrics)
        previous = self._last_capture
        if previous is not None and panel.overlay_chk.isChecked():
            self.plot.set_reference(previous[0], t_trig - previous[1])
        self._last_capture = (packet, t_trig)
        self._last_metrics = metrics
        text = f"Triggered at {t_trig:.3f} s (paused; Resume for live view)"
        self.lbl_status.setText(text + (f": {note}" if note else ""))
        self.lbl_status.setStyleSheet("color: #FFB74D; font-weight: bold;")

    def closeEvent(self, event: QtGui.QCloseEvent | None) -> None:
        """
        Stops the engine and its thread before the window goes away (C6).

        The stop runs as a *blocking* queued call, so the serial port is closed on the engine
        thread before its event loop is asked to quit. The thread is never terminated.
        """
        if not self._shut_down:
            self._shut_down = True
            if self.engine_thread.isRunning():
                QtCore.QMetaObject.invokeMethod(
                    self.engine,
                    "stop_working",
                    QtCore.Qt.ConnectionType.BlockingQueuedConnection,
                )
                self.engine_thread.quit()
                if not self.engine_thread.wait(2000):
                    logger.error("Engine thread did not finish within 2 s of quit()")

        if event is not None:
            event.accept()
