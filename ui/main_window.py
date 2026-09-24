"""
Main Application Window Module.

This module defines the primary GUI window that integrates the control panel,
plotting area, and the background telemetry engine. It acts as the **Controller**
in the application architecture, managing high-level signal wiring, thread
lifecycle, and global events.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets

from core.acquisition.engine import TelemetryEngine
from core.acquisition.storage import StreamStores
from core.config import DEFAULT_CONFIG_PATH, StreamConfigLoader
from core.protocol.stats import LinkReport, format_link_report
from core.types import EngineState, StreamConfig
from ui.charts.live_feed import LiveFeed
from ui.charts.telemetry_plot import TelemetryPlot
from ui.config.tab import ConfiguratorTab
from ui.panels.container import MainControlPanel

logger = logging.getLogger(__name__)


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

    def __init__(self, config_path: Path = DEFAULT_CONFIG_PATH) -> None:
        """
        Initializes the main window, UI layout, and background engine.

        `config_path` is the streams.json to use; one loader for it is shared by the
        dashboard and the Configuration tab.
        """
        super().__init__()
        self.stream_loader = StreamConfigLoader(config_path)

        # --- State Tracking ---
        # Mirror of the engine's state, updated only from `state_changed` (never read across
        # threads).
        self.engine_state: EngineState = EngineState.IDLE
        self._shut_down = False

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
        self.panel = MainControlPanel(self.stream_loader)
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

        self.status_bar.addWidget(self.lbl_status)
        self.status_bar.addPermanentWidget(self.lbl_link)
        self.status_bar.addPermanentWidget(self.lbl_cursor)

        # --- Engine & Thread Initialization ---
        # Retrieve initial settings via the Panel's public API
        initial_period = self.panel.get_initial_sample_period()
        initial_samples = self.panel.get_initial_sample_count()

        # One store per stream, shared: the engine's reader thread writes them, and the GUI
        # pulls from the store of the stream it shows.
        self.stores = StreamStores(initial_samples)
        self.engine: TelemetryEngine = TelemetryEngine(
            initial_period, initial_samples, stores=self.stores
        )
        self.live_feed = LiveFeed(
            None, self.plot, lambda: self.panel.time_panel.get_period() / 1000.0, parent=self
        )

        self.engine_thread: QtCore.QThread = QtCore.QThread(self)
        self.engine.moveToThread(self.engine_thread)
        self.engine_thread.finished.connect(self.engine.deleteLater)
        self.engine_thread.finished.connect(self.engine_thread.deleteLater)
        self.engine_thread.start()

        # --- Signal Wiring ---

        # 1. Configuration: Panel -> Engine
        self.panel.stream_changed.connect(self._on_stream_changed)
        self.panel.time_config_changed.connect(self.engine.update_time_config)
        self.panel.pid_left_sent.connect(self.engine.send_left_config)
        self.panel.pid_right_sent.connect(self.engine.send_right_config)
        self.panel.pid_all_sent.connect(self.engine.send_all_config)

        # 2. Control Logic: Panel -> Main Window
        self.panel.connection_requested.connect(self._handle_connection)
        self.panel.pause_requested.connect(self._handle_pause)

        # 3. Visuals: Panel -> Plot
        self.panel.signal_visibility_changed.connect(self.plot.set_signal_visible)
        self.panel.signal_visibility_changed.connect(lambda *_: self.live_feed.invalidate())
        self.panel.time_config_changed.connect(lambda *_: self.live_feed.invalidate())

        # 4. Engine -> UI (small signals only; plot data is pulled by LiveFeed)
        self.engine.status_msg.connect(self.lbl_status.setText)
        self.engine.connection_failed.connect(self._handle_connection_failed)
        self.engine.state_changed.connect(self._on_engine_state_changed)
        self.engine.streams_configured.connect(self._bind_live_feed)
        self.engine.link_stats.connect(self._on_link_stats)

        # 6. Interactivity: Plot -> UI
        self.plot.cursor_moved.connect(self.lbl_cursor.setText)

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
        """Surfaces streams.json problems in the status bar (no modal dialog at startup)."""
        problems = self.panel.stream_loader.problems
        for problem in problems:
            logger.warning("streams.json: %s", problem)
        if not problems:
            return
        errors = sum(p.severity == "error" for p in problems)
        summary = f"streams.json: {len(problems)} problem(s)"
        if errors:
            summary += f", {errors} stream(s) not loaded"
        self.lbl_status.setText(summary + " (hover for details)")
        self.lbl_status.setToolTip("\n".join(str(p) for p in problems))
        self.lbl_status.setStyleSheet("color: #FFB74D; font-weight: bold;")

    def _configure_engine_streams(self) -> None:
        """Tells the engine to decode every valid stream (A1); the GUI picks one to show."""
        QtCore.QMetaObject.invokeMethod(
            self.engine,
            "configure_streams",
            QtCore.Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(dict, dict(self.stream_loader.list_streams())),
        )

    def _bind_live_feed(self) -> None:
        """Points the live feed at the shown stream's store (after stores are (re)built)."""
        self.live_feed.set_store(self.stores.get(self.panel.current_stream_key()))

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
        self.plot.configure_signals(stream_cfg["signals"])
        self._bind_live_feed()
        key = self.panel.current_stream_key()
        if key is not None:
            QtCore.QMetaObject.invokeMethod(
                self.engine,
                "select_stream",
                QtCore.Qt.ConnectionType.QueuedConnection,
                QtCore.Q_ARG(str, key),
            )

    def _handle_connection(self, port: str, baud: int) -> None:
        """
        Handles connection requests triggered by the Control Panel.
        """
        if port != "STOP":
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
        if state == EngineState.RUNNING:
            self.lbl_status.setStyleSheet("color: #4CAF50; font-weight: bold;")

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
        self.plot.set_paused(paused, self.live_feed.freeze() if paused else None)
        if not paused:
            self.live_feed.invalidate()
        if update_status:
            self.lbl_status.setText("PAUSED" if paused else "Connected")

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
