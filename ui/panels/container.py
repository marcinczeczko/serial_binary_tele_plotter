"""
Main Control Panel Container Module.

This module aggregates specialized sub-panels into a single sidebar widget.
It implements the **Facade Pattern**, exposing a unified signal interface to the
MainWindow while delegating specific UI logic to independent child components
(Connection, PID, Timing, Signals).
"""

from PyQt6 import QtCore, QtWidgets

from core.config import StreamConfigLoader
from ui.panels.connection import ConnectionPanel
from ui.panels.pid import PidTuningPanel
from ui.panels.signals import SignalListPanel
from ui.panels.timing import TimeConfigPanel


class MainControlPanel(QtWidgets.QWidget):
    """
    The main sidebar widget containing all configuration controls.

    This class instantiates sub-panels and acts as a central hub for UI signals.
    It hides the complexity of the widget hierarchy from the MainWindow.

    Attributes:
        connection_requested (pyqtSignal): Emitted for serial connection/disconnection.
        pause_requested (pyqtSignal): Emitted to toggle plot pausing.
        pid_config_sent (pyqtSignal): Emitted to update PID parameters.
        motor_changed (pyqtSignal): Emitted when the selected motor changes.
        time_config_changed (pyqtSignal): Emitted when sampling settings change.
        stream_changed (pyqtSignal): Emitted when the stream definition JSON changes.
        scale_changed (pyqtSignal): Emitted when manual signal scaling is applied.
        signal_visibility_changed (pyqtSignal): Emitted when a signal is toggled.
    """

    # --- Public Signals (Re-exported for MainWindow) ---

    # Connection signals
    connection_requested = QtCore.pyqtSignal(str, int)
    pause_requested = QtCore.pyqtSignal(bool)

    # PID / Motor signals
    # Payload: (ramp_type, motor_id, kp, ki, kff, alpha, rps)
    pid_config_sent = QtCore.pyqtSignal(int, int, float, float, float, float, float)
    motor_changed = QtCore.pyqtSignal(int)

    # Timing signals
    time_config_changed = QtCore.pyqtSignal(float, int)

    # Stream / Signals configuration
    stream_changed = QtCore.pyqtSignal(dict)
    scale_changed = QtCore.pyqtSignal(str, float, float)
    signal_visibility_changed = QtCore.pyqtSignal(str, bool)

    def __init__(self):
        """Initializes the layout, instantiates sub-panels, and wires signals."""
        super().__init__()

        # Main Layout Settings
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(10)

        # 1. Instantiate Sub-Panels
        self.conn_panel = ConnectionPanel()
        self.pid_panel = PidTuningPanel()
        self.time_panel = TimeConfigPanel()
        self.sig_panel = SignalListPanel()

        # 2. Stream Selector
        # This widget is small enough to be kept directly in the container
        # rather than having its own dedicated file.
        self.grp_stream = QtWidgets.QGroupBox("Stream Type")
        l_stream = QtWidgets.QVBoxLayout(self.grp_stream)

        self.stream_loader = StreamConfigLoader("streams.json")
        self.payload_combo = QtWidgets.QComboBox()

        # Populate streams from config file
        for sid, s in self.stream_loader.list_streams().items():
            self.payload_combo.addItem(s["name"], sid)

        self.payload_combo.currentIndexChanged.connect(self._on_stream_selection)
        l_stream.addWidget(self.payload_combo)

        # 3. Assemble Layout (Order is visually important)
        layout.addWidget(self.conn_panel)
        layout.addWidget(self.pid_panel)
        layout.addWidget(self.time_panel)
        layout.addWidget(self.grp_stream)

        # The signal list gets the 'stretch' factor (1) to fill remaining space
        # at the bottom of the sidebar.
        layout.addWidget(self.sig_panel, 1)

        # 4. Signal Wiring (Forwarding)
        self._connect_signals()

        # --- Initialization Fix ---
        # Force initial load of the signal list based on the default combo selection.
        # Without this, the 'Signals' panel would be empty on startup.
        if self.payload_combo.count() > 0:
            self._on_stream_selection(self.payload_combo.currentIndex())

    def _connect_signals(self) -> None:
        """
        Wires internal sub-panel signals to this container's public signals.
        This allows MainWindow to connect to 'self.panel' without knowing about internal structure.
        """

        # Connection Panel -> Self
        self.conn_panel.connection_requested.connect(self.connection_requested)
        self.conn_panel.pause_requested.connect(self.pause_requested)

        # PID Panel -> Self
        self.pid_panel.pid_config_sent.connect(self.pid_config_sent)
        self.pid_panel.motor_changed.connect(self.motor_changed)

        # Time Panel -> Self
        self.time_panel.time_config_changed.connect(self.time_config_changed)

        # Signal List Panel -> Self
        self.sig_panel.scale_changed.connect(self.scale_changed)
        self.sig_panel.signal_visibility_changed.connect(self.signal_visibility_changed)

    def _on_stream_selection(self, idx: int) -> None:
        """
        Internal handler for combo box change events.

        Args:
            idx (int): The new index of the combo box.
        """
        sid = self.payload_combo.itemData(idx)
        if not sid:
            return

        # Load config object
        cfg = self.stream_loader.get_stream(sid)

        # 1. Update the UI list immediately (Sub-panel responsibility)
        self.apply_stream_config(cfg)

        # 2. Notify MainWindow to reconfigure Engine and Plotter
        self.stream_changed.emit(cfg)

    # --- Public API (Methods called by MainWindow) ---

    def apply_stream_config(self, cfg: dict) -> None:
        """
        Delegates the rebuilding of the signal list to the SignalListPanel.
        """
        self.sig_panel.rebuild_list(cfg)

    def refresh_ports(self) -> None:
        """Delegates port refreshing to the ConnectionPanel."""
        self.conn_panel.refresh_ports()

    # --- Accessors for initial values (used by MainWindow init) ---

    def get_initial_sample_period(self) -> float:
        """Returns the current sampling period from the timing panel."""
        return self.time_panel.get_period()

    def get_initial_sample_count(self) -> int:
        """Returns the current buffer size from the timing panel."""
        return self.time_panel.get_samples()

    def get_current_stream_config(self) -> dict:
        """
        Returns the configuration of the currently selected stream.
        Useful for MainWindow initialization to avoid accessing internal widgets.
        """
        idx = self.payload_combo.currentIndex()
        sid = self.payload_combo.itemData(idx)
        if sid:
            return self.stream_loader.get_stream(sid)
        return {}
