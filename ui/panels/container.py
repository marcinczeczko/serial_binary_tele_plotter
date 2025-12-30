"""
Main Control Panel Container.

This module aggregates specialized sub-panels into a single sidebar widget.
It acts as a Facade, exposing a unified signal interface to the MainWindow
while delegating specific UI logic to child components.
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

    This class instantiates sub-panels (Connection, PID, Time, Signals)
    and wires their internal signals to the public signals exposed below.
    """

    # --- Public Signals (Re-exported for MainWindow) ---

    # Connection
    connection_requested = QtCore.pyqtSignal(str, int)
    pause_requested = QtCore.pyqtSignal(bool)

    # PID / Motor
    pid_config_sent = QtCore.pyqtSignal(int, int, float, float, float, float, float)
    motor_changed = QtCore.pyqtSignal(int)

    # Timing
    time_config_changed = QtCore.pyqtSignal(float, int)

    # Stream / Signals
    stream_changed = QtCore.pyqtSignal(dict)
    scale_changed = QtCore.pyqtSignal(str, float, float)
    signal_visibility_changed = QtCore.pyqtSignal(str, bool)

    def __init__(self):
        """Initializes the layout and instantiates sub-panels."""
        super().__init__()

        # Główne ustawienia layoutu
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(10)

        # 1. Instantiate Sub-Panels
        self.conn_panel = ConnectionPanel()
        self.pid_panel = PidTuningPanel()
        self.time_panel = TimeConfigPanel()
        self.sig_panel = SignalListPanel()

        # 2. Stream Selector (Small enough to keep here directly)
        self.grp_stream = QtWidgets.QGroupBox("Stream Type")
        l_stream = QtWidgets.QVBoxLayout(self.grp_stream)

        self.stream_loader = StreamConfigLoader("streams.json")
        self.payload_combo = QtWidgets.QComboBox()

        # Populate streams from config
        for sid, s in self.stream_loader.list_streams().items():
            self.payload_combo.addItem(s["name"], sid)

        self.payload_combo.currentIndexChanged.connect(self._on_stream_selection)
        l_stream.addWidget(self.payload_combo)

        # 3. Assemble Layout (Order matters!)
        layout.addWidget(self.conn_panel)
        layout.addWidget(self.pid_panel)
        layout.addWidget(self.time_panel)
        layout.addWidget(self.grp_stream)
        # The signal list gets the 'stretch' factor (1) to fill remaining space
        layout.addWidget(self.sig_panel, 1)

        # 4. Signal Wiring (Forwarding)
        self._connect_signals()

        # --- FIX: Force initial load of the signal list ---
        # Wywołujemy to ręcznie, aby wypełnić listę sygnałów przy starcie aplikacji
        if self.payload_combo.count() > 0:
            self._on_stream_selection(self.payload_combo.currentIndex())

    def _connect_signals(self):
        """Wires internal sub-panel signals to this container's public signals."""

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

    def _on_stream_selection(self, idx: int):
        """Internal handler for combo box change."""
        sid = self.payload_combo.itemData(idx)
        if not sid:
            return

        # Load config and emit
        cfg = self.stream_loader.get_stream(sid)

        # Update the UI list immediately
        self.apply_stream_config(cfg)

        # Notify MainWindow to reconfigure Engine/Plot
        self.stream_changed.emit(cfg)

    # --- Public API (Methods called by MainWindow) ---

    def apply_stream_config(self, cfg: dict):
        """
        Delegates the rebuilding of the signal list to the SignalListPanel.
        """
        self.sig_panel.rebuild_list(cfg)

    def refresh_ports(self):
        """Delegates port refreshing to the ConnectionPanel."""
        self.conn_panel.refresh_ports()

    # Accessors for initial values (used by MainWindow init)
    def get_initial_sample_period(self) -> float:
        return self.time_panel.get_period()

    def get_initial_sample_count(self) -> int:
        return self.time_panel.get_samples()

    def get_current_stream_config(self) -> dict:
        """Returns the configuration of the currently selected stream."""
        idx = self.payload_combo.currentIndex()
        sid = self.payload_combo.itemData(idx)
        if sid:
            return self.stream_loader.get_stream(sid)
        return {}
