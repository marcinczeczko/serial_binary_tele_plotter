"""
Main Application Window.

This module defines the primary GUI window that integrates the control panel,
plotting area, and the background telemetry engine. It handles high-level
signal wiring, thread management, and application lifecycle events.
"""

from PyQt6 import QtCore, QtWidgets

from core.acquisition.engine import TelemetryEngine
from core.types import EngineState
from ui.charts.telemetry_plot import TelemetryPlot
from ui.panels.container import MainControlPanel


class MainWindow(QtWidgets.QMainWindow):
    """
    The main window of the DiffBot Telemetry Viewer.

    This class acts as the central hub of the application. It:
    1. Instantiates the UI components (ControlPanel, PlotArea).
    2. Sets up the background engine thread (`TelemetryEngine`) for data processing.
    3. Connects signals and slots between the UI and the engine to ensure thread safety.
    4. Manages the status bar and global application events (like closing).
    """

    def __init__(self):
        """
        Initializes the main window, UI layout, and background engine.
        """
        super().__init__()
        self.setWindowTitle("DiffBot Telemetry Viewer (Pro)")
        self.resize(1280, 800)

        # Status Bar Initialization
        self.status_bar = QtWidgets.QStatusBar()
        self.setStatusBar(self.status_bar)

        self.lbl_status = QtWidgets.QLabel("Ready")
        self.lbl_cursor = QtWidgets.QLabel("")

        self.status_bar.addWidget(self.lbl_status)
        self.status_bar.addPermanentWidget(self.lbl_cursor)

        # Main Layout (Splitter)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        self.panel = MainControlPanel()
        self.plot = TelemetryPlot()

        splitter.addWidget(self.panel)
        splitter.addWidget(self.plot)
        splitter.setSizes([350, 930])

        # Engine & Thread Initialization
        # --- FIX 1: Używamy getterów z panelu zamiast sięgać do widgetów bezpośrednio ---
        initial_period = self.panel.get_initial_sample_period()
        initial_samples = self.panel.get_initial_sample_count()

        self.engine = TelemetryEngine(initial_period, initial_samples)
        # --------------------------------------------------------------------------------

        self.engine_thread = QtCore.QThread()
        self.engine.moveToThread(self.engine_thread)
        self.engine_thread.start()

        # --- Signal Wiring ---

        # Panel -> Engine (Configuration)
        self.panel.scale_changed.connect(self.engine.update_scale)
        self.panel.stream_changed.connect(self._on_stream_changed)
        self.panel.time_config_changed.connect(self.engine.update_time_config)
        self.panel.pid_config_sent.connect(self.engine.send_pid_config)

        # Panel -> Main Window (Connection logic)
        self.panel.connection_requested.connect(self._handle_connection)
        self.panel.pause_requested.connect(self._handle_pause)

        # Panel -> Plot (Visuals)
        self.panel.signal_visibility_changed.connect(self.plot.set_signal_visible)

        # Engine -> Plot/UI (Data flow)
        self.engine.data_ready.connect(self.plot.on_data_ready)
        self.engine.status_msg.connect(self.lbl_status.setText)

        # Motor filter
        self.panel.motor_changed.connect(self.engine.set_selected_motor)

        # Plot -> UI (Interactivity)
        self.plot.cursor_moved.connect(self.lbl_cursor.setText)

        # Setup initial stream (AFTER wiring signals)
        self._initial_stream_setup()

    def _initial_stream_setup(self):
        """
        Loads configuration for the currently selected stream in the panel.
        """
        # --- FIX 2: Panel nie udostępnia już publicznie loadera ani combo boxa.
        # Najprościej: symulujemy wybór strumienia, pobierając aktualne ID.
        # Ale ponieważ MainControlPanel ukrył te detale, musimy albo:
        # A) Dodać metody dostępowe w MainControlPanel (get_current_stream_config)
        # B) Wymusić emisję sygnału zmiany strumienia przy starcie.

        # Opcja B jest czystsza architektonicznie:
        # Wywołujemy "sztuczną" zmianę indeksu w panelu, żeby wszystko się przeładowało.
        # Jednak panel już to zrobił w swoim __init__. My potrzebujemy tylko configu dla Engine/Plot.

        # Dlatego dodajmy metodę pomocniczą do MainControlPanel (zobacz poniżej w komentarzu co dodać do container.py)
        # Zakładając, że masz tam metodę `get_current_stream_config()`:

        cfg = self.panel.get_current_stream_config()
        if not cfg:
            return

        # Aplikujemy config do wykresu i silnika (Panel zrobił to sobie sam w swoim init)
        self.plot.configure_signals(cfg["signals"])
        self.engine.configure_signals(cfg["signals"])
        self.engine.configure_frame(cfg)

    def _on_stream_changed(self, stream_cfg: dict):
        if self.engine.state == EngineState.RUNNING:
            QtCore.QMetaObject.invokeMethod(
                self.engine,
                "stop_working",
                QtCore.Qt.ConnectionType.QueuedConnection,
            )
            self.lbl_status.setText("Stream changed — stopped")
            self.lbl_status.setStyleSheet("color: #FFA000; font-weight: bold;")

        self.plot.configure_signals(stream_cfg["signals"])
        self.engine.configure_signals(stream_cfg["signals"])
        self.engine.configure_frame(stream_cfg)

    def _handle_connection(self, port, baud):
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
            self.lbl_status.setText(f"Connected to {port}")
            self.lbl_status.setStyleSheet("color: #4CAF50; font-weight: bold;")
        else:
            QtCore.QMetaObject.invokeMethod(
                self.engine, "stop_working", QtCore.Qt.ConnectionType.QueuedConnection
            )
            self.lbl_status.setText("Disconnected")
            self.lbl_status.setStyleSheet("color: #F44336; font-weight: bold;")

    def _handle_pause(self, paused):
        self.plot.set_paused(paused)
        self.lbl_status.setText("PAUSED" if paused else "Connected")

    def closeEvent(self, event):
        """
        Handles the application close event to ensure clean thread termination.
        """
        QtCore.QMetaObject.invokeMethod(
            self.engine, "stop_working", QtCore.Qt.ConnectionType.QueuedConnection
        )

        self.engine_thread.quit()

        if not self.engine_thread.wait(2000):
            print("Engine thread hung, forcing termination...")
            self.engine_thread.terminate()
            self.engine_thread.wait()

        event.accept()
