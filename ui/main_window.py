"""
Main Application Window Module.

This module defines the primary GUI window that integrates the control panel,
plotting area, and the background telemetry engine. It acts as the **Controller**
in the application architecture, managing high-level signal wiring, thread
lifecycle, and global events.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from PyQt6 import QtCore, QtGui, QtWidgets

from core.acquisition.engine import TelemetryEngine
from core.acquisition.storage import StreamStores
from core.acquisition.timebase import time_base_config
from core.analysis.export import ExportError, export_table, parquet_available
from core.analysis.step_response import StepMetrics, step_metrics
from core.analysis.trigger import TriggerSpec
from core.config import DEFAULT_CONFIG_PATH, SCHEMA_VERSION, StreamConfigLoader, list_profiles
from core.protocol.commands import CommandDef, CommandError, encode_command, resolve_values
from core.protocol.stats import LinkReport, format_link_report
from core.recording.sbtp import SUFFIX as RECORDING_SUFFIX
from core.recording.sbtp import recording_name
from core.types import EngineState, PlotMode, PlotPacketWithBounds, StreamConfig
from styles import AMBER, ORANGE, RED, TEXT, TEXT_DIM
from ui.app_settings import (
    DEFAULT_RECORDINGS_DIR,
    KEY_CONFIG_PATH,
    KEY_RECORD_ON_CONNECT,
    KEY_RECORDINGS_DIR,
    add_recent_profile,
    app_settings,
    profiles_dir,
    recent_profiles,
)
from ui.charts.live_feed import LiveFeed
from ui.charts.telemetry_plot import TelemetryPlot
from ui.charts.trigger_controller import TriggerController
from ui.common.numbers import format_number
from ui.config.tab import ConfiguratorTab
from ui.panels.command_log import CommandLog, LogEntry
from ui.panels.command_panel import CommandPanel, SendRequest
from ui.panels.container import MainControlPanel
from ui.panels.profile_dialog import ProfileDialog
from ui.panels.top_bar import (
    LinkHealth,
    MessageLabel,
    PartsButton,
    Square,
    Text,
    TopBar,
    format_duration,
)
from ui.panes import EdgeTab, PaneState, RightView, edge_strip
from ui.ui_state import UiState

logger = logging.getLogger(__name__)

EDGE_GLYPHS = {"rising": "╱", "falling": "╲", "either": "╳"}  # the bar's trigger edge
MAX_MARKERS = 200  # per stream; older ones have long left the buffer


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


def _popup_button(content: QtWidgets.QWidget) -> PartsButton:
    """A top-bar item that opens `content` in a popup below it."""
    button = PartsButton()
    button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
    menu = QtWidgets.QMenu(button)
    action = QtWidgets.QWidgetAction(menu)
    action.setDefaultWidget(content)
    menu.addAction(action)
    button.setMenu(menu)
    return button


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
        self.resize(1440, 900)

        # The dashboard's controls; their pieces are placed below (R6.1).
        self.panel = MainControlPanel(self.stream_loader, self.ui_state)
        self.panel.setParent(self)
        self._select_connection()
        self.plot = TelemetryPlot()

        # --- Top bar pieces (R9.2); placed in `_build_top_bar` once the menus exist ---
        self.top_bar = TopBar()
        self.lbl_status = MessageLabel()
        self.time_btn = _popup_button(self.panel.time_panel)
        self.trigger_btn = _popup_button(self.panel.trigger_panel.setup)
        self.record_btn = PartsButton()
        self.lbl_link = LinkHealth()
        self._link_dialog: QtWidgets.QDialog | None = None
        self._link_dialog_text = QtWidgets.QLabel()

        # --- Panes (R9.3, ADR-0012): Signals | plot | Tune or Step, opened from edge tabs ---
        self.command_log = CommandLog()
        self.controls_view = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.controls_view.addWidget(self.panel.controls_stack)
        self.controls_view.addWidget(self.command_log)
        self.controls_view.setStretchFactor(0, 3)
        self.controls_view.setStretchFactor(1, 1)
        self.right_stack = QtWidgets.QStackedWidget()
        self.right_stack.addWidget(self.controls_view)
        self.right_stack.addWidget(self.panel.trigger_panel.results)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        for pane in (self.panel.sig_panel, self.plot, self.right_stack):
            self.splitter.addWidget(pane)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.splitterMoved.connect(lambda _pos, _i: self._remember_pane_widths())
        self.signals_tab = EdgeTab("SIGNALS", "left")
        self.signals_tab.setToolTip("Signals  [")
        self.tune_tab = EdgeTab("TUNE", "right")
        self.step_tab = EdgeTab("STEP", "right")
        self.step_tab.setToolTip("Step response")
        self.signals_tab.clicked.connect(self.toggle_signals_pane)
        self.tune_tab.clicked.connect(lambda: self._set_panes(self.panes.click_right("tune")))
        self.step_tab.clicked.connect(lambda: self._set_panes(self.panes.click_right("step")))
        root = QtWidgets.QWidget()
        root_layout = QtWidgets.QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self.top_bar)
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(edge_strip([self.signals_tab], "left"))
        body.addWidget(self.splitter, 1)
        body.addWidget(edge_strip([self.tune_tab, self.step_tab], "right"))
        root_layout.addLayout(body, 1)
        self.setCentralWidget(root)
        self.controls_title = ""
        self.panes = self.ui_state.panes()

        # --- Configuration editor: its own window (File > Edit profile) ---
        self.configurator = ConfiguratorTab(self.stream_loader)
        self.configurator.config_saved.connect(self._reload_configuration)
        self.configurator.listen_requested.connect(
            lambda seconds: self._invoke("listen_lines", QtCore.Q_ARG(float, seconds))
        )
        self.configurator.stop_listening_requested.connect(lambda: self._invoke("stop_listening"))
        self.config_window = QtWidgets.QDialog(self)
        self.config_window.setWindowTitle(f"Configuration: {self.stream_loader.path.name}")
        self.config_window.resize(1300, 860)
        config_layout = QtWidgets.QVBoxLayout(self.config_window)
        config_layout.setContentsMargins(0, 0, 0, 0)
        config_layout.addWidget(self.configurator)

        # Command markers (R6.3): per stream, (time on that stream's time base, label).
        self._markers: dict[str, list[tuple[float, str]]] = {}
        self._next_marker = 1
        # Stream activity (R6.1): samples stored per stream at the last statistics tick.
        self._activity: tuple[float, dict[str, int]] | None = None
        self._rec_started: float | None = None

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
        self.panel.samples_changed.connect(lambda _n: self._update_time_button())
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
        self.engine.status_msg.connect(lambda text: self._say(text))
        self.engine.connection_failed.connect(self._handle_connection_failed)
        self.engine.state_changed.connect(self._on_engine_state_changed)
        self.engine.streams_configured.connect(self._bind_live_feed)
        self.engine.link_stats.connect(self._on_link_stats)
        self.engine.lines_heard.connect(self.configurator.on_lines_heard)
        self.engine.session_ended.connect(self._on_session_ended)
        self.engine.recording_changed.connect(self._on_recording_changed)

        # 5. Trigger capture and step response (R4.4, R4.5)
        trigger_panel = self.panel.trigger_panel
        trigger_panel.arm_requested.connect(self._arm_trigger)
        trigger_panel.disarm_requested.connect(self.trigger.disarm)
        self.trigger.state_changed.connect(trigger_panel.show_state)
        self.trigger.captured.connect(self._on_trigger_captured)

        # 6. Interactivity: Plot -> UI
        self.plot.readout_changed.connect(self.panel.sig_panel.show_readout)

        # 7. Phase 6 layout: trigger on the plot, controls dock, log (R6.3, R6.5)
        self.plot.trigger_level_changed.connect(trigger_panel.set_level)
        trigger_panel.changed.connect(self._update_trigger_ui)
        self.panel.controls_changed.connect(self._on_controls_changed)
        self.panel.terminal.line_entered.connect(self._send_line)
        self.command_log.resend_requested.connect(self._resend)

        self._build_menus()
        self._build_top_bar()
        conn = self.panel.conn_panel
        conn.profile_chosen.connect(lambda path: self.switch_profile(Path(path)))
        conn.new_profile_requested.connect(self.new_profile)
        conn.open_profile_requested.connect(self._open_profile_file)
        conn.edit_profile_requested.connect(self.show_configuration)
        self._refresh_profiles()
        self._update_title()
        self._update_trigger_ui()
        self.panel.show_controls()  # the right tab's label, now that it's wired
        geometry = self.ui_state.window_geometry()
        if geometry is not None:
            self.restoreGeometry(geometry)
        self._apply_panes()

        # --- Final Setup ---
        self._apply_profile()
        self._configure_engine_streams()
        self._initial_stream_setup()
        self._report_config_problems()

    def _reload_configuration(self) -> None:
        """Re-reads streams.json after the Configuration tab saved it, keeping the selection."""
        try:
            self.panel.reload_streams()
        except ValueError as e:
            self._say(f"Could not reload streams.json: {e}", "error")
            return
        self._apply_profile()
        self._configure_engine_streams()
        self._refresh_profiles()
        self._update_title()
        self._say("Configuration reloaded from disk.")
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
                f"schema {loader.source_version} read as {SCHEMA_VERSION}; save it from "
                "File > Edit profile to update the file"
            )
            details += loader.migration_notes
        if not parts:
            return
        text = "streams.json: " + "; ".join(parts) + " (hover for details)"
        self._say(text, "warn", "\n".join(details))

    # --- device profiles (R8.2) ------------------------------------------------------------

    def _select_connection(self) -> None:
        """This profile's port and baud; else the last port, at the profile's baud."""
        conn = self.panel.conn_panel
        own = self.ui_state.connection(fallback=False)
        if own is not None:
            conn.select(*own)
            return
        last = self.ui_state.connection()
        baud = self.stream_loader.profile.baud or (last[1] if last else 0)
        conn.select(last[0] if last else "", baud)

    def _apply_profile(self) -> None:
        """Tells the engine the profile's name and wire format (the decoder to use)."""
        profile = self.stream_loader.profile
        self._invoke(
            "configure_profile", QtCore.Q_ARG(str, profile.name), QtCore.Q_ARG(str, profile.format)
        )

    def _update_title(self) -> None:
        profile = self.stream_loader.profile
        self.setWindowTitle(
            f"Serial Binary Plotter - {profile.name} ({self.stream_loader.path.name})"
        )
        self.config_window.setWindowTitle(
            f"Profile: {profile.name} ({self.stream_loader.path.name})"
        )

    def _refresh_profiles(self) -> None:
        """The profile menu: the profiles folder, the bundled file, recent and current files."""
        extra: list[str | Path] = [
            DEFAULT_CONFIG_PATH,
            *recent_profiles(self.settings),
            self.stream_loader.path,
        ]
        entries = list_profiles(profiles_dir(self.settings), extra)
        self.panel.conn_panel.set_profiles(
            entries, self.stream_loader.path, self.stream_loader.profile
        )

    def switch_profile(self, path: Path) -> bool:
        """
        Shows another device profile: its streams, panels, remembered view, port and baud,
        and the decoder for its format. Only while disconnected (the device changes).
        """
        if self.engine_state == EngineState.RUNNING:
            self._say("Disconnect before switching profiles", "warn")
            return False
        if path.expanduser().resolve() == self.stream_loader.path.expanduser().resolve():
            self._refresh_profiles()
            return True
        try:
            self.stream_loader.open(path)
        except (OSError, ValueError) as e:
            QtWidgets.QMessageBox.warning(self, "Cannot open profile", str(e))
            self._refresh_profiles()
            return False
        self.ui_state.set_config(self.stream_loader.path)
        self.panes = self.ui_state.panes()
        self.settings.setValue(KEY_CONFIG_PATH, str(self.stream_loader.path))
        if self.stream_loader.path.parent.resolve() != profiles_dir(self.settings).resolve():
            add_recent_profile(self.settings, self.stream_loader.path)
        self._markers.clear()
        self.plot.set_markers([])
        self.panel.reload_profile()
        self._select_connection()
        self.configurator.reload_document()
        self._apply_profile()
        self._configure_engine_streams()
        self._refresh_profiles()
        self._update_title()
        profile = self.stream_loader.profile
        self._say(
            f"Profile {profile.name} ({profile.format})", tooltip=str(self.stream_loader.path)
        )
        self._report_config_problems()
        return True

    def profile_dialog(self) -> ProfileDialog:
        return ProfileDialog(
            profiles_dir(self.settings),
            self.stream_loader.profile.name,
            self.stream_loader.data,
            self,
        )

    def new_profile(self) -> None:
        dialog = self.profile_dialog()
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.create_profile(dialog.path(), dialog.document())

    def create_profile(self, path: Path, doc: dict[str, Any]) -> bool:
        """Writes a new profile file (never over an existing one) and switches to it."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as f:
                json.dump(doc, f, indent=4)
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "Cannot create profile", str(e))
            return False
        if not self.switch_profile(path):
            return False
        self.show_configuration()
        return True

    def _open_profile_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open a profile", str(profiles_dir(self.settings)), "Profiles (*.json)"
        )
        if path:
            self.switch_profile(Path(path))

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
        key = self.panel.current_stream_key()
        self.plot.set_markers(self._markers.get(key or "", []))
        self._bind_live_feed()
        self._show_period()
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
        self._update_time_button()

    def _update_time_button(self) -> None:
        """`Window 10 s`: the time the plot shows (period × samples); amber while the
        period is overridden. Click: the period and samples."""
        tp = self.panel.time_panel
        period_ms, samples = tp.get_period(), tp.get_samples()
        color = AMBER if tp.is_overridden() else TEXT
        self.time_btn.set_parts(
            [
                Text("Window", TEXT_DIM, number=False),
                Text(format_duration(period_ms * samples / 1000), color),
            ]
        )
        self.time_btn.setToolTip(
            f"Time shown: period {format_number(period_ms)} ms × {samples} samples"
            + (" (period overridden for this session)" if tp.is_overridden() else "")
            + ". Click to change."
        )

    def _on_period_changed(self, period_ms: float) -> None:
        """The user overrode the shown stream's period: re-time that stream (all history)."""
        cfg = self.panel.get_current_stream_config()
        key = self.panel.current_stream_key()
        if cfg is None or key is None or period_ms <= 0:
            return
        scale_s = period_ms / 1000.0 / time_base_config(cfg).step
        self._scale_overrides[key] = scale_s
        self._update_time_button()
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
            self._say(f"Connecting to {port}...")
        else:
            QtCore.QMetaObject.invokeMethod(
                self.engine, "stop_working", QtCore.Qt.ConnectionType.QueuedConnection
            )
            self._set_pause_state(False, update_status=False)
            self._say("Disconnected")

    def _on_engine_state_changed(self, state: EngineState) -> None:
        self.engine_state = state
        running = state == EngineState.RUNNING
        self.configurator.set_connected(running)
        self.panel.terminal.set_connected(running)
        # The Connect button mirrors the engine, however the session started (menu replay).
        self.panel.conn_panel.set_connected(running)
        self._update_menus()
        if running:
            # A new session starts new stream time: markers of the last one no longer apply.
            self._markers.clear()
            self.plot.set_markers([])
            self._activity = None
            if self.act_record_on_connect.isChecked() and not self._replaying:
                self._start_recording()
        else:
            self._replaying = False
            self._activity = None
            for key in self.stores.keys():
                self.panel.stream_tabs.set_activity(key, None)
            self._update_menus()

    def _on_session_ended(self, message: str) -> None:
        """A replay reached its end: the data stays for analysis."""
        self._say(message)

    def _handle_connection_failed(self, message: str) -> None:
        """
        Syncs UI state after a connection failure from the worker.
        """
        self.panel.conn_panel.set_connected(False)
        self._set_pause_state(False, update_status=False)
        self._say(message, "error")

    def _on_link_stats(self, report: LinkReport) -> None:
        self.lbl_link.show_report(*format_link_report(report))
        self.top_bar.set_shown(self.lbl_link, self.lbl_link.has_problems)
        if self._link_dialog is not None:
            self._link_dialog_text.setText(self._link_statistics_text())
        if report["format"] == "text":  # the editor's Line view shows the newest lines
            self.configurator.set_last_lines(report["last_lines"], report["last_unmatched"])
            if report["replies"] or report["replies_dropped"]:  # the terminal's (R8.5)
                self.panel.terminal.add_replies(
                    time.strftime("%H:%M:%S"), report["replies"], report["replies_dropped"]
                )
        self._update_activity()

    def _update_activity(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Each stream's sample rate on its tab, from the stores' counts (R6.1)."""
        now = clock()
        totals = {}
        for key in self.stores.keys():
            store = self.stores.get(key)
            totals[key] = store.total_stored if store is not None else 0
        previous = self._activity
        self._activity = (now, totals)
        if previous is None or now <= previous[0]:
            return
        dt = now - previous[0]
        for key, total in totals.items():
            rate = (total - previous[1].get(key, total)) / dt
            self.panel.stream_tabs.set_activity(key, rate)
        self._update_record_button()

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
        if update_status:  # the RUN/STOP box shows it; the message says what it means
            self._say("Stopped: measuring the frozen view" if paused else "Running")

    # --- commands (R5.2) -----------------------------------------------------------------

    def _send_command(self, request: SendRequest) -> None:
        """
        Encodes a panel button's command and hands the packet to the engine; logs it, marks
        it on the plot and tells the panel what was sent (R6.3).
        """
        command = self.stream_loader.commands.get(request.button.command)
        if command is None:
            return  # a panel only offers valid commands; kept for reloads in flight
        label = request.button.label
        if self.engine_state != EngineState.RUNNING:
            self._command_status(f"Not connected: '{label}' not sent", error=True)
            if not request.live:  # Live edits while disconnected aren't worth a log row each
                self._log_refused(label, "not connected")
            return
        try:
            values = resolve_values(command, request.params, request.column, request.button.values)
            packet = encode_command(command, values)
        except CommandError as e:
            self._command_status(f"Not sent: {e}", error=True)
            self._log_refused(label, str(e))
            return
        self.send_packet.emit(packet)
        sent = _sent_params(command, request)
        panel = self.panel.control_panels.get(request.panel)
        detail = _describe_change(panel, sent) + (" · live" if request.live else "")
        if panel is not None:
            panel.mark_sent(sent)
        number = self._mark_send(label)
        self.command_log.add(
            LogEntry(number, time.strftime("%H:%M:%S"), label, detail, packet, command.key)
        )
        self._command_status(
            f"Sent '{label}': {command.label} (ID 0x{command.packet_id:02X}, {len(packet)} B)"
        )

    def _resend(self, entry: LogEntry) -> None:
        """Sends a logged packet again, exactly as it was."""
        if self.engine_state != EngineState.RUNNING:
            self._command_status(f"Not connected: '{entry.label}' not sent again", error=True)
            return
        self.send_packet.emit(entry.packet)
        number = self._mark_send(entry.label)
        self.command_log.add(
            LogEntry(
                number,
                time.strftime("%H:%M:%S"),
                entry.label,
                f"again (as ▲ {entry.number})",
                entry.packet,
                entry.command,
            )
        )
        self._command_status(f"Sent '{entry.label}' again ({len(entry.packet)} B)")

    def _on_controls_changed(self, title: str) -> None:
        self.controls_title = title or "Controls"
        is_text = self.panel.is_text
        self.command_log.setVisible(not is_text)  # the terminal has its own
        # A text profile's right pane is its terminal (R8.5); it has no step response.
        self.tune_tab.setText("TERMINAL" if is_text else "TUNE")
        self.tune_tab.setToolTip(f"{self.controls_title}  ]")
        self.tune_tab.updateGeometry()
        self.step_tab.setVisible(not is_text)
        if is_text and self.panes.view == "step":
            self._set_panes(replace(self.panes, view="tune"))
        else:
            self._apply_panes()

    # --- panes (R9.3) -------------------------------------------------------------------

    def toggle_signals_pane(self) -> None:
        self._set_panes(self.panes.toggle_left())

    def toggle_right_pane(self) -> None:
        self._set_panes(self.panes.toggle_right())

    def toggle_both_panes(self) -> None:
        self._set_panes(self.panes.toggle_both())

    def show_right_view(self, view: RightView) -> None:
        self._set_panes(replace(self.panes, right_open=True, view=view))

    def _set_panes(self, state: PaneState) -> None:
        self.panes = state
        self._apply_panes()
        self.ui_state.set_panes(self.panes)  # as applied: a text profile has no Step view

    def _apply_panes(self) -> None:
        """Shows the panes, the right view and the lit tabs as `self.panes` says."""
        p = self.panes
        if self.panel.is_text and p.view == "step":
            p = self.panes = replace(p, view="tune")
        self.panel.sig_panel.setVisible(p.left_open)
        self.right_stack.setVisible(p.right_open)
        self.right_stack.setCurrentWidget(
            self.controls_view if p.view == "tune" else self.panel.trigger_panel.results
        )
        left = p.left_width if p.left_open else 0
        right = p.right_width if p.right_open else 0
        self.splitter.setSizes([left, max(self.splitter.width() - left - right, 1), right])
        self.signals_tab.set_lit(p.left_open)
        self.tune_tab.set_lit(p.right_open and p.view == "tune")
        self.step_tab.set_lit(p.right_open and p.view == "step")
        self.act_signals_pane.setChecked(p.left_open)
        self.act_right_pane.setChecked(p.right_open)

    def _remember_pane_widths(self) -> None:
        """A dragged splitter: the open panes' new widths, kept per profile."""
        left, _plot, right = self.splitter.sizes()
        p = self.panes
        self._set_panes(
            replace(
                p,
                left_width=left if p.left_open and left > 0 else p.left_width,
                right_width=right if p.right_open and right > 0 else p.right_width,
            )
        )

    def showEvent(self, event: QtGui.QShowEvent | None) -> None:  # noqa: N802
        super().showEvent(event)
        self._apply_panes()  # the splitter has its real width now

    def _send_line(self, line: str) -> None:
        """A text profile's terminal line (R8.5): ASCII plus the chosen ending, numbered."""
        terminal = self.panel.terminal
        when = time.strftime("%H:%M:%S")
        if self.engine_state != EngineState.RUNNING:
            terminal.add_refused(when, line, "not connected")
            self._command_status("Not connected: line not sent", error=True)
            return
        try:
            data = line.encode("ascii") + terminal.ending
        except UnicodeEncodeError:
            terminal.add_refused(when, line, "only ASCII can be sent")
            self._command_status("Not sent: only ASCII can be sent", error=True)
            return
        self.send_packet.emit(data)
        number = self._mark_send(line)
        terminal.add_sent(number, when, line)
        self._command_status(f"Sent ▲ {number}: {line}")

    def _log_refused(self, label: str, reason: str) -> None:
        self.command_log.add(LogEntry(None, time.strftime("%H:%M:%S"), label, reason))

    def _mark_send(self, label: str) -> int:
        """Numbers a send and marks it on every stream with data, at that stream's now."""
        number = self._next_marker
        self._next_marker += 1
        for key in self.stores.keys():
            store = self.stores.get(key)
            t = store.latest_time_s() if store is not None else None
            if t is not None:
                markers = self._markers.setdefault(key, [])
                markers.append((t, f"▲ {number} {label}"))
                del markers[:-MAX_MARKERS]
        self.plot.set_markers(self._markers.get(self.panel.current_stream_key() or "", []))
        return number

    def _say(self, text: str, level: str = "info", tooltip: str = "") -> None:
        """The top bar's message (R9.2): info fades; warnings and errors stay."""
        self.lbl_status.say(text, level, tooltip)

    def _command_status(self, text: str, error: bool = False) -> None:
        self._say(text, "error" if error else "info")

    # --- menus: recording, replay, export (R4.1-R4.3) -----------------------------------

    def _build_menus(self) -> None:
        bar = self.menuBar()
        assert bar is not None
        file_menu = bar.addMenu("&File")
        view_menu = bar.addMenu("&View")
        rec_menu = bar.addMenu("&Recording")
        assert file_menu is not None and view_menu is not None and rec_menu is not None
        # Single keys, as on a scope's front panel. A focused text field keeps its keys.
        self.act_signals_pane = _action(
            view_menu, "Signals pane", self.toggle_signals_pane, checkable=True, on_toggle=False
        )
        self.act_signals_pane.setShortcut(QtGui.QKeySequence("["))
        self.act_right_pane = _action(
            view_menu, "Right pane", self.toggle_right_pane, checkable=True, on_toggle=False
        )
        self.act_right_pane.setShortcut(QtGui.QKeySequence("]"))
        self.act_plot_only = _action(view_menu, "Plot only (both panes)", self.toggle_both_panes)
        self.act_plot_only.setShortcut(QtGui.QKeySequence("\\"))
        view_menu.addSeparator()
        self.act_link_stats = _action(view_menu, "Link statistics", self.show_link_statistics)
        view_menu.addSeparator()
        self.act_reset_view = _action(view_menu, "Reset view to the profile", self.panel.reset_view)

        self.act_new_profile = _action(file_menu, "New profile…", self.new_profile)
        self.act_open_profile = _action(file_menu, "Open profile file…", self._open_profile_file)
        self.act_edit_config = _action(file_menu, "Edit profile…", self.show_configuration)
        self.act_edit_config.setShortcut(QtGui.QKeySequence("Ctrl+,"))
        file_menu.addSeparator()
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

    def _build_top_bar(self) -> None:
        """
        One row (R9.2, ADR-0012): profile | port, Connect | RUN/STOP | stream tabs |
        message | Window | Trigger, then REC and link problems only while they're true.
        """
        conn = self.panel.conn_panel
        bar = self.top_bar
        bar.add(conn.profile_btn)
        bar.add(conn.port_combo, conn.baud_combo, conn.connect_btn, spacing=8)
        bar.add(conn.pause_btn)
        bar.add(self.panel.stream_tabs)
        bar.add_stretch(self.lbl_status)
        bar.add_divider()
        bar.add(self.time_btn)
        bar.add(self.trigger_btn, divider=False)
        self.record_btn.setCheckable(True)
        self.record_btn.clicked.connect(lambda _=False: self.act_record.trigger())
        bar.add_optional(self.record_btn)
        bar.add_optional(self.lbl_link)
        conn.setParent(bar)  # the owner of the moved widgets; never shown itself
        conn.hide()
        self._update_record_button()
        self._update_time_button()
        pause = QtGui.QShortcut(QtGui.QKeySequence("Space"), self)
        pause.activated.connect(self.panel.conn_panel.pause_btn.click)

    def _link_statistics_text(self) -> str:
        link = self.lbl_link
        if not link.rate:
            return "No data yet: connect to see the link's statistics."
        return f"{link.rate}\n{link.details}"

    def show_link_statistics(self) -> None:
        """View → Link statistics: every counter, live while the window is open."""
        if self._link_dialog is None:
            dialog = QtWidgets.QDialog(self)
            dialog.setWindowTitle("Link statistics")
            layout = QtWidgets.QVBoxLayout(dialog)
            self._link_dialog_text = QtWidgets.QLabel()
            self._link_dialog_text.setTextInteractionFlags(
                QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
            )
            layout.addWidget(self._link_dialog_text)
            self._link_dialog = dialog
        self._link_dialog_text.setText(self._link_statistics_text())
        self._link_dialog.show()
        self._link_dialog.raise_()

    def show_configuration(self) -> None:
        self.config_window.show()
        self.config_window.raise_()
        self.config_window.activateWindow()

    def _update_record_button(self) -> None:
        """REC, only while recording: a red square and the time so far (click stops)."""
        recording = bool(self._recording_path)
        self.top_bar.set_shown(self.record_btn, recording)
        self.record_btn.setChecked(recording)
        self.record_btn.setEnabled(self.act_record.isEnabled())
        if recording and self._rec_started is not None:
            elapsed = int(time.monotonic() - self._rec_started)
            self.record_btn.set_parts(
                [Square(RED), Text(f"REC {elapsed // 60:02d}:{elapsed % 60:02d}", RED, px=12)]
            )
            self.record_btn.setToolTip(f"Recording to {self._recording_path} (Ctrl+R stops)")
        else:
            self.record_btn.set_parts([Text("REC", number=False, px=12)])  # hidden
            self.record_btn.setToolTip("Record the session's raw bytes (Ctrl+R)")

    def _update_trigger_ui(self) -> None:
        """
        The bar's `Trigger`; armed, in orange with the source's colour, the edge and
        level, then ARMED. The level line shows on the plot while armed.
        """
        panel = self.panel.trigger_panel
        state = panel.state
        spec = panel.spec()
        if state in ("armed", "fired") and spec is not None:
            cfg = self.panel.get_current_stream_config() or {}
            sig = (cfg.get("signals") or {}).get(spec.signal) or {}
            word = "ARMED" if state == "armed" else "CAPTURE"
            self.trigger_btn.set_parts(
                [
                    Square(str(sig.get("color", TEXT)), size=10),
                    Text("Trigger", ORANGE, number=False, bold=True),
                    Text(
                        f"{EDGE_GLYPHS.get(spec.edge, '')} {format_number(spec.level)}",
                        ORANGE,
                        bold=True,
                    ),
                    Text(word, ORANGE, number=False, bold=True, px=12),
                ]
            )
            self.trigger_btn.setToolTip(f"{panel.summary()} · {word}")
            self.plot.set_trigger_level(spec.signal, spec.level)
        else:
            self.trigger_btn.set_parts([Text("Trigger", number=False)])
            self.trigger_btn.setToolTip("Trigger: capture a step (a signal crossing a level)")
            self.plot.set_trigger_level(None)

    def _update_menus(self) -> None:
        running = self.engine_state == EngineState.RUNNING
        for act in (self.act_new_profile, self.act_open_profile):
            act.setEnabled(not running)  # a profile switch needs a disconnected device
        self.act_record.setEnabled(running or bool(self._recording_path))
        self.act_replay_pause.setEnabled(running and self._replaying)
        self.act_replay_step.setEnabled(running and self._replaying)
        if not (running and self._replaying):
            self.act_replay_pause.setChecked(False)
        self._update_record_button()

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
        self._rec_started = time.monotonic() if path else None
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
        self._say(f"Replaying {Path(path).name}…")

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
        self._say(message, "error" if message.startswith("Export failed") else "info")

    # --- trigger capture and step response (R4.4, R4.5) ---------------------------------

    def _arm_trigger(self, spec: TriggerSpec) -> None:
        if self.engine_state != EngineState.RUNNING:
            self.panel.trigger_panel.set_armed(False)
            self._say("Connect first: the trigger watches live data", "warn")
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
        spec = panel.spec()
        if spec is not None:  # shade what came before the trigger
            self.plot.set_capture_window((t_trig - spec.pre_s, t_trig))
        self.show_right_view("step")
        text = f"Triggered at {t_trig:.3f} s (paused; Resume for live view)"
        self._say(text + (f": {note}" if note else ""), "warn")

    def closeEvent(self, event: QtGui.QCloseEvent | None) -> None:
        """
        Stops the engine and its thread before the window goes away (C6).

        The stop runs as a *blocking* queued call, so the serial port is closed on the engine
        thread before its event loop is asked to quit. The thread is never terminated.
        """
        if not self._shut_down:
            self._shut_down = True
            self.ui_state.set_window_geometry(self.saveGeometry())
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


def _sent_params(command: CommandDef, request: SendRequest) -> dict[str, dict[str, float]]:
    """The panel parameters a send carried: {column: {parameter: value}}."""
    sent: dict[str, dict[str, float]] = {}
    for f in command.fields:
        if f.param is None or f.name in request.button.values or f.value is not None:
            continue
        col = f.column if f.column is not None else request.column
        if col is not None and f.param in request.params.get(col, {}):
            sent.setdefault(col, {})[f.param] = request.params[col][f.param]
    return sent


def _describe_change(panel: CommandPanel | None, sent: dict[str, dict[str, float]]) -> str:
    """What a send changed since the last one, e.g. "kp 0.1 → 0.25 (Left, Right)"."""
    if panel is None or not sent:
        return ""
    changes: dict[tuple[str, float, float], list[str]] = {}
    first = True
    for col, params in sent.items():
        for key, value in params.items():
            before = panel.last_sent(col, key)
            if before is None:
                continue
            first = False
            if abs(before - value) > 1e-12:
                changes.setdefault((key, before, value), []).append(col)
    if first:
        return "first send"
    if not changes:
        return "same values"
    parts = []
    for (key, before, value), cols in changes.items():
        where = f" ({', '.join(c for c in cols if c)})" if any(cols) and len(sent) > 1 else ""
        parts.append(f"{key} {before:g} → {value:g}{where}")
    return "; ".join(parts[:3]) + (" …" if len(parts) > 3 else "")
