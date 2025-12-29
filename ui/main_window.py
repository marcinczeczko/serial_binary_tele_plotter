"""
Main Application Window.

This module defines the primary GUI window that integrates the control panel,
plotting area, and the background telemetry worker. It handles high-level
signal wiring, thread management, and application lifecycle events.
"""

from PyQt6 import QtCore, QtWidgets

from core.worker import TelemetryWorker
from ui.panels import ControlPanel
from ui.plot_area import PlotArea


class MainWindow(QtWidgets.QMainWindow):
    """
    The main window of the DiffBot Telemetry Viewer.

    This class acts as the central hub of the application. It:
    1. Instantiates the UI components (ControlPanel, PlotArea).
    2. Sets up the background worker thread (`TelemetryWorker`) for data processing.
    3. Connects signals and slots between the UI and the worker to ensure thread safety.
    4. Manages the status bar and global application events (like closing).
    """

    def __init__(self):
        """
        Initializes the main window, UI layout, and background worker.

        Sets up:
        - Window title and dimensions.
        - Status bar widgets.
        - Central splitter layout containing the Control Panel and Plot Area.
        - The `TelemetryWorker` running in a separate `QThread`.
        - All signal-slot connections for data flow and control logic.
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

        self.panel = ControlPanel()
        self.plot = PlotArea()

        splitter.addWidget(self.panel)
        splitter.addWidget(self.plot)
        splitter.setSizes([350, 930])

        # Worker & Thread Initialization
        self.worker = TelemetryWorker(
            self.panel.sample_period_edit.value(), self.panel.sample_count_edit.value()
        )

        self.worker_thread = QtCore.QThread()
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.start()

        # --- Signal Wiring ---
        # Panel -> Worker (Configuration)
        self.panel.scale_changed.connect(self.worker.update_scale)
        self.panel.stream_changed.connect(self._on_stream_changed)
        self.panel.time_config_changed.connect(self.worker.update_time_config)
        self.panel.pid_config_sent.connect(self.worker.send_pid_config)

        # Panel -> Main Window (Connection logic)
        self.panel.connection_requested.connect(self._handle_connection)
        self.panel.pause_requested.connect(self._handle_pause)

        # Panel -> Plot (Visuals)
        self.panel.signal_visibility_changed.connect(self.plot.set_signal_visible)

        # Worker -> Plot/UI (Data flow)
        self.worker.data_ready.connect(self.plot.on_data_ready)
        self.worker.status_msg.connect(self.lbl_status.setText)

        # Plot -> UI (Interactivity)
        self.plot.cursor_moved.connect(self.lbl_cursor.setText)

        # Initial Stream Setup
        self.panel._on_stream_changed(self.panel.payload_combo.currentIndex())

    def _on_stream_changed(self, stream_cfg: dict):
        # Stop data acquisition if is progress
        QtCore.QMetaObject.invokeMethod(
            self.worker,
            "stop_working",
            QtCore.Qt.ConnectionType.QueuedConnection,
        )

        # UI
        self.plot.configure_signals(stream_cfg["signals"])

        # Worker
        self.worker.configure_signals(stream_cfg["signals"])
        self.worker.configure_frame(stream_cfg)

    def _handle_connection(self, port, baud):
        """
        Handles connection requests triggered by the Control Panel.

        Uses `invokeMethod` to safely communicate with the worker thread.

        Args:
            port (str): The serial port name (e.g., "COM3") or "STOP" to disconnect.
            baud (int): The baud rate for the connection.
        """
        if port != "STOP":
            QtCore.QMetaObject.invokeMethod(
                self.worker,
                "start_working",
                QtCore.Qt.ConnectionType.QueuedConnection,
                QtCore.Q_ARG(str, port),
                QtCore.Q_ARG(int, baud),
            )
            self.lbl_status.setText(f"Connected to {port}")
            self.lbl_status.setStyleSheet("color: #4CAF50; font-weight: bold;")
        else:
            QtCore.QMetaObject.invokeMethod(
                self.worker, "stop_working", QtCore.Qt.ConnectionType.QueuedConnection
            )
            self.lbl_status.setText("Disconnected")
            self.lbl_status.setStyleSheet("color: #F44336; font-weight: bold;")

    def _handle_pause(self, paused):
        """
        Toggles the pause state of the plotter.

        Args:
            paused (bool): True to pause the plot (enter Analysis mode), False to resume live plotting.
        """
        self.plot.set_paused(paused)
        self.lbl_status.setText("PAUSED" if paused else "Connected")

    def closeEvent(self, event):
        """
        Handles the application close event to ensure clean thread termination.

        Stops the worker safely using `invokeMethod`, quits the thread loop,
        and waits for the thread to finish before closing the window.
        """
        # Command the worker to stop its internal timer/loop
        QtCore.QMetaObject.invokeMethod(
            self.worker, "stop_working", QtCore.Qt.ConnectionType.QueuedConnection
        )

        # Tell the thread event loop to exit
        self.worker_thread.quit()

        # Wait for the thread to finish (with timeout to prevent freezing)
        if not self.worker_thread.wait(2000):
            print("Wątek nie odpowiedział, wymuszam zamknięcie...")
            self.worker_thread.terminate()
            self.worker_thread.wait()

        event.accept()
