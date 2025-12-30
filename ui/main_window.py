"""
Main Application Window Module.

This module defines the primary GUI window that integrates the control panel,
plotting area, and the background telemetry engine. It acts as the **Controller**
in the application architecture, managing high-level signal wiring, thread
lifecycle, and global events.
"""

from PyQt6 import QtCore, QtGui, QtWidgets

from core.acquisition.engine import TelemetryEngine
from core.types import EngineState
from ui.charts.telemetry_plot import TelemetryPlot
from ui.panels.container import MainControlPanel


class MainWindow(QtWidgets.QMainWindow):
    """
    The main window of the DiffBot Telemetry Viewer.

    This class serves as the central hub of the application. Its responsibilities include:
    1. **Composition**: Instantiating the UI components (ControlPanel, PlotArea).
    2. **Threading**: Setting up the background engine thread (`TelemetryEngine`).
    3. **Wiring**: Connecting signals/slots between the UI (Main Thread) and
       the Engine (Worker Thread).
    4. **Lifecycle**: Managing startup configuration and safe shutdown sequences.
    """

    def __init__(self):
        """
        Initializes the main window, UI layout, and background engine.
        """
        super().__init__()

        # --- State Tracking ---
        self.active_port = None
        self.active_baud = 115200

        # --- Window Setup ---
        self.setWindowTitle("DiffBot Telemetry Viewer (Pro)")
        self.resize(1280, 800)

        # --- Status Bar Initialization ---
        self.status_bar = QtWidgets.QStatusBar()
        self.setStatusBar(self.status_bar)

        self.lbl_status = QtWidgets.QLabel("Ready")
        self.lbl_cursor = QtWidgets.QLabel("")

        self.status_bar.addWidget(self.lbl_status)
        self.status_bar.addPermanentWidget(self.lbl_cursor)

        # --- Main Layout (Splitter) ---
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        # Instantiate the main view components
        self.panel = MainControlPanel()
        self.plot = TelemetryPlot()

        splitter.addWidget(self.panel)
        splitter.addWidget(self.plot)
        # Set initial ratio (Panel : Plot)
        splitter.setSizes([350, 930])

        # --- Engine & Thread Initialization ---
        # Retrieve initial settings via the Panel's public API (Facade)
        initial_period = self.panel.get_initial_sample_period()
        initial_samples = self.panel.get_initial_sample_count()

        self.engine = TelemetryEngine(initial_period, initial_samples)

        # Move the Engine object to a dedicated background thread.
        self.engine_thread = QtCore.QThread()
        self.engine.moveToThread(self.engine_thread)
        self.engine_thread.start()

        # --- Signal Wiring ---

        # 1. Configuration: Panel -> Engine
        self.panel.scale_changed.connect(self.engine.update_scale)
        self.panel.stream_changed.connect(self._on_stream_changed)
        self.panel.time_config_changed.connect(self.engine.update_time_config)
        self.panel.pid_config_sent.connect(self.engine.send_pid_config)
        self.panel.imu_command_sent.connect(self.engine.send_imu_command)

        # 2. Control Logic: Panel -> Main Window
        self.panel.connection_requested.connect(self._handle_connection)
        self.panel.pause_requested.connect(self._handle_pause)

        # 3. Visuals: Panel -> Plot
        self.panel.signal_visibility_changed.connect(self.plot.set_signal_visible)

        # 4. Data Flow: Engine -> Plot/UI
        self.engine.data_ready.connect(self.plot.on_data_ready)
        self.engine.status_msg.connect(self.lbl_status.setText)

        # 5. Logic: Motor selection filters
        self.panel.motor_changed.connect(self.engine.set_selected_motor)

        # 6. Interactivity: Plot -> UI
        self.plot.cursor_moved.connect(self.lbl_cursor.setText)

        # --- Final Setup ---
        # Configure the plot and engine based on the default selected stream
        self._initial_stream_setup()

    def _initial_stream_setup(self) -> None:
        """
        Loads configuration for the currently selected stream in the panel.
        """
        # Fetch config from the Panel Facade
        cfg = self.panel.get_current_stream_config()
        if not cfg:
            return

        # Apply configuration to components
        self.plot.configure_signals(cfg["signals"])
        self.engine.configure_signals(cfg["signals"])
        self.engine.configure_frame(cfg)

    def _on_stream_changed(self, stream_cfg: dict) -> None:
        """
        Handles the event when the user selects a different stream type in the panel.
        It safely restarts the engine if it was running.
        """
        was_running = self.engine.state == EngineState.RUNNING

        # 1. Update Plot (Main Thread UI - Immediate)
        self.plot.configure_signals(stream_cfg["signals"])

        # 2. Queue Stop Command (Worker Thread)
        if was_running:
            QtCore.QMetaObject.invokeMethod(
                self.engine,
                "stop_working",
                QtCore.Qt.ConnectionType.QueuedConnection,
            )

        # 3. Queue Configuration Commands (Worker Thread)
        # We use invokeMethod to ensure these happen AFTER 'stop_working' in the queue.
        # Direct calls would race with the stop command.
        QtCore.QMetaObject.invokeMethod(
            self.engine,
            "configure_signals",
            QtCore.Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(dict, stream_cfg["signals"]),
        )
        QtCore.QMetaObject.invokeMethod(
            self.engine,
            "configure_frame",
            QtCore.Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(dict, stream_cfg),
        )

        # 4. Queue Restart Command (Worker Thread)
        # Only if it was running previously
        if was_running and self.active_port:
            self.lbl_status.setText(f"Switching stream... ({self.active_port})")

            QtCore.QMetaObject.invokeMethod(
                self.engine,
                "start_working",
                QtCore.Qt.ConnectionType.QueuedConnection,
                QtCore.Q_ARG(str, self.active_port),
                QtCore.Q_ARG(int, self.active_baud),
            )
        else:
            self.lbl_status.setText("Stream configuration loaded.")

    def _handle_connection(self, port: str, baud: int) -> None:
        """
        Handles connection requests triggered by the Control Panel.
        """
        if port != "STOP":
            # Store connection details for auto-reconnect logic
            self.active_port = port
            self.active_baud = baud

            # Start the Engine
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
            # Clear active port but keep baud preference maybe?
            self.active_port = None

            # Stop the Engine
            QtCore.QMetaObject.invokeMethod(
                self.engine, "stop_working", QtCore.Qt.ConnectionType.QueuedConnection
            )
            self.lbl_status.setText("Disconnected")
            self.lbl_status.setStyleSheet("color: #F44336; font-weight: bold;")

    def _handle_pause(self, paused: bool) -> None:
        """
        Toggles the pause state of the plotter.
        """
        self.plot.set_paused(paused)
        self.lbl_status.setText("PAUSED" if paused else "Connected")

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """
        Handles the application close event to ensure clean thread termination.
        """
        # 1. Stop the worker logic
        QtCore.QMetaObject.invokeMethod(
            self.engine, "stop_working", QtCore.Qt.ConnectionType.QueuedConnection
        )

        # 2. Quit the thread loop
        self.engine_thread.quit()

        # 3. Wait for cleanup (with timeout to prevent freezing the app on close)
        if not self.engine_thread.wait(2000):
            print("Engine thread hung, forcing termination...")
            self.engine_thread.terminate()
            self.engine_thread.wait()

        event.accept()
