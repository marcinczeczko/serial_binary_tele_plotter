"""
Main Control Panel Container Module.

This module aggregates specialized sub-panels into a single sidebar widget.
It uses **QStackedWidget** to dynamically switch the central control panel
(e.g., PID Tuning vs Empty) based on the active stream type.
"""

from typing import Dict

from PyQt6 import QtCore, QtWidgets

from core.config import StreamConfigLoader
from ui.common.widgets import CollapsableSection
from ui.panels.connection import ConnectionPanel
from ui.panels.imu import ImuCalibrationPanel
from ui.panels.pid import PidTuningPanel
from ui.panels.signals import SignalListPanel
from ui.panels.timing import TimeConfigPanel


class MainControlPanel(QtWidgets.QWidget):
    """
    The main sidebar widget containing all configuration controls.
    """

    # --- Public Signals ---
    connection_requested = QtCore.pyqtSignal(str, int)
    pause_requested = QtCore.pyqtSignal(bool)
    pid_left_sent = QtCore.pyqtSignal(int, float, float, float, float, float)
    pid_right_sent = QtCore.pyqtSignal(int, float, float, float, float, float)
    run_test_sent = QtCore.pyqtSignal(float, float)

    time_config_changed = QtCore.pyqtSignal(float, int)
    stream_changed = QtCore.pyqtSignal(dict)
    signal_visibility_changed = QtCore.pyqtSignal(str, bool)
    imu_command_sent = QtCore.pyqtSignal(int)

    def __init__(self):
        super().__init__()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(10)

        # 1. Fixed Top Panel (Connection)
        self.conn_panel = ConnectionPanel()

        # 2. Stream Selection (Shared)
        self.grp_stream = QtWidgets.QGroupBox("Stream Type")
        l_stream = QtWidgets.QVBoxLayout(self.grp_stream)
        self.stream_loader = StreamConfigLoader("streams.json")
        self.payload_combo = QtWidgets.QComboBox()

        for sid, s in self.stream_loader.list_streams().items():
            self.payload_combo.addItem(s["name"], sid)

        self.payload_combo.currentIndexChanged.connect(self._on_stream_selection)
        l_stream.addWidget(self.payload_combo)

        # 3. Dynamic Stacked Panel (Context-Aware UI)
        self.dynamic_stack = QtWidgets.QStackedWidget()

        # -- A: PID Panel --
        self.pid_panel = PidTuningPanel()
        self.pid_section = CollapsableSection("PID Tunning", self.pid_panel)
        self.imu_panel = ImuCalibrationPanel()
        self.imu_section = CollapsableSection("IMU Calibration", self.imu_panel)

        # -- B: Empty Panel (for streams with no controls) --
        self.empty_panel = QtWidgets.QWidget()
        # l_empty = QtWidgets.QVBoxLayout(self.empty_panel)
        # l_empty.addWidget(QtWidgets.QLabel("No settings for this stream."))

        self.dynamic_stack.addWidget(self.empty_panel)  # Index 0
        self.dynamic_stack.addWidget(self.pid_section)  # Index 1
        self.dynamic_stack.addWidget(self.imu_section)

        # Map string keys from JSON ('panel_type') to widget instances
        self.panel_map: Dict[str, QtWidgets.QWidget] = {
            "none": self.empty_panel,
            "pid": self.pid_section,
            "imu": self.imu_section,
        }

        # 4. Fixed Bottom Panels
        self.time_panel = TimeConfigPanel()
        self.sig_panel = SignalListPanel()

        # 5. Assemble Main Layout
        layout.addWidget(self.conn_panel)
        layout.addWidget(self.grp_stream)

        # Insert the dynamic stack here
        layout.addWidget(self.dynamic_stack)

        layout.addWidget(self.time_panel)
        layout.addWidget(self.sig_panel, 1)

        # 6. Wiring & Init
        self._connect_signals()

        if self.payload_combo.count() > 0:
            self._on_stream_selection(self.payload_combo.currentIndex())

    def _connect_signals(self) -> None:
        """Wires internal signals. All panels are wired even if hidden."""
        # Global
        self.conn_panel.connection_requested.connect(self.connection_requested)
        self.conn_panel.pause_requested.connect(self.pause_requested)
        self.time_panel.time_config_changed.connect(self.time_config_changed)
        self.sig_panel.signal_visibility_changed.connect(self.signal_visibility_changed)

        # PID Panel
        self.pid_panel.pid_left_sent.connect(self.pid_left_sent)
        self.pid_panel.pid_right_sent.connect(self.pid_right_sent)
        self.pid_panel.run_test_sent.connect(self.run_test_sent)

        # IMU Panel
        self.imu_panel.calibration_requested.connect(self.imu_command_sent)

    def _on_stream_selection(self, idx: int) -> None:
        """
        Handles switching the data config AND the visible control UI.
        """
        sid = self.payload_combo.itemData(idx)
        if not sid:
            return

        cfg = self.stream_loader.get_stream(sid)

        # --- DYNAMIC UI SWITCHING ---
        # 1. Get type from config (default to 'none')
        panel_type = cfg.get("panel_type", "none")

        # --- FIX: Hide the stack completely if panel_type is 'none' ---
        if panel_type == "none":
            self.dynamic_stack.setVisible(False)
        else:
            # Resolve widget from map and show it
            target_widget = self.panel_map.get(panel_type, self.empty_panel)
            self.dynamic_stack.setCurrentWidget(target_widget)
            self.dynamic_stack.setVisible(True)
        # ----------------------------

        # Update Signal List & Notify Main Window
        self.sig_panel.rebuild_list(cfg)
        self.stream_changed.emit(cfg)

    # --- Public API ---

    def get_initial_sample_period(self) -> float:
        return self.time_panel.get_period()

    def get_initial_sample_count(self) -> int:
        return self.time_panel.get_samples()

    def get_current_stream_config(self) -> dict:
        idx = self.payload_combo.currentIndex()
        sid = self.payload_combo.itemData(idx)
        if sid:
            return self.stream_loader.get_stream(sid)
        return {}

    def reload_streams(self) -> None:
        """
        Reloads configuration from disk and refreshes the UI list.
        This allows external controllers to force a refresh safely.
        """
        # 1. Reload JSON data (using the new public method)
        self.stream_loader.load()

        # 2. Refresh Combo Box content
        self.payload_combo.blockSignals(True)
        self.payload_combo.clear()

        for sid, s in self.stream_loader.list_streams().items():
            self.payload_combo.addItem(s["name"], sid)

        self.payload_combo.blockSignals(False)

        # 3. Force selection of the first item
        if self.payload_combo.count() > 0:
            self.payload_combo.setCurrentIndex(0)
            # Explicitly call the handler to ensure the app state syncs up
            # (Calling internal method from within the class is allowed)
            self._on_stream_selection(0)
