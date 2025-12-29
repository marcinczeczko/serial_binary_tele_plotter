"""
Control Panel UI module.

This module provides the widget containing all user controls, including:
- Serial port configuration and connection management.
- PID parameter tuning.
- Time window and sampling configuration.
- Stream selection and dynamic signal list generation.
"""

from PyQt6 import QtCore, QtWidgets
from serial.tools import list_ports

from core.loader import StreamConfigLoader
from ui.widgets import CollapsibleGroup, YAxisControlWidget


class ControlPanel(QtWidgets.QWidget):
    """
    The main control widget for the Telemetry Viewer.

    This class aggregates various configuration inputs and emits signals when
    the user interacts with them. It handles the dynamic generation of signal
    controls based on the selected stream configuration.

    Signals:
        scale_changed (str, float, float): Emitted when manual scale application is requested.
                                           Payload: (signal_id, min, max).
        stream_changed (dict): Emitted when the stream type is changed.
                               Payload: configuration dict for signals.
        time_config_changed (float, int): Emitted when sampling settings change.
                                          Payload: (period_ms, sample_count).
        signal_visibility_changed (str, bool): Emitted when a signal checkbox is toggled.
                                               Payload: (signal_id, is_visible).
        signal_lock_changed (str, bool): Emitted when Y-axis lock is toggled (reserved).
        connection_requested (str, int): Emitted when Connect/Disconnect is clicked.
                                         Payload: (port_name, baudrate) or ("STOP", 0).
        pause_requested (bool): Emitted when Pause/Resume is clicked.
        pid_config_sent (int, float, float, float): Emitted when PID update is requested.
                                                    Payload: (motor_id, kp, ki, kff).
    """

    scale_changed = QtCore.pyqtSignal(str, float, float)
    stream_changed = QtCore.pyqtSignal(dict)
    time_config_changed = QtCore.pyqtSignal(float, int)
    signal_visibility_changed = QtCore.pyqtSignal(str, bool)
    signal_lock_changed = QtCore.pyqtSignal(str, bool)
    connection_requested = QtCore.pyqtSignal(str, int)
    pause_requested = QtCore.pyqtSignal(bool)
    pid_config_sent = QtCore.pyqtSignal(int, float, float, float)

    def __init__(self):
        """Initializes the Control Panel UI layout and widgets."""
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)

        # --- Serial Connection Group ---
        grp_serial = QtWidgets.QGroupBox("Serial Connection")
        l_serial = QtWidgets.QGridLayout(grp_serial)
        self.port_combo = QtWidgets.QComboBox()
        self.refresh_btn = QtWidgets.QPushButton("⟳")
        self.refresh_btn.clicked.connect(self.refresh_ports)
        self.baud_combo = QtWidgets.QComboBox()
        self.baud_combo.addItems(["115200", "230400", "460800", "921600"])
        l_serial.addWidget(QtWidgets.QLabel("Port:"), 0, 0)
        l_serial.addWidget(self.port_combo, 0, 1)
        l_serial.addWidget(self.refresh_btn, 0, 2)
        l_serial.addWidget(QtWidgets.QLabel("Baud:"), 1, 0)
        l_serial.addWidget(self.baud_combo, 1, 1, 1, 2)
        layout.addWidget(grp_serial)

        # --- Connection Buttons ---
        btn_layout = QtWidgets.QHBoxLayout()
        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.setCheckable(True)
        self.connect_btn.setStyleSheet(
            "QPushButton:checked { background-color: #C62828; } QPushButton { background-color: #2E7D32; font-weight: bold; }"
        )
        self.connect_btn.toggled.connect(self._on_connect_toggled)
        self.pause_btn = QtWidgets.QPushButton("Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.setEnabled(False)
        self.pause_btn.toggled.connect(self._on_pause_toggled)
        btn_layout.addWidget(self.connect_btn)
        btn_layout.addWidget(self.pause_btn)
        layout.addLayout(btn_layout)

        # --- PID Tuning Group ---
        grp_pid = QtWidgets.QGroupBox("PID Tuning")
        l_pid = QtWidgets.QGridLayout(grp_pid)
        self.motor_selector = QtWidgets.QComboBox()
        self.motor_selector.addItem("Left Motor", 0)
        self.motor_selector.addItem("Right Motor", 1)
        self.motor_selector.addItem("Both Motors", 2)
        self.kp_sb = self._make_sb(1.0)
        self.ki_sb = self._make_sb(0.1)
        self.kff_sb = self._make_sb(0.5)
        self.pid_update_btn = QtWidgets.QPushButton("Update PID Parameters")
        self.pid_update_btn.clicked.connect(self._on_pid_update)
        l_pid.addWidget(QtWidgets.QLabel("Motor:"), 0, 0)
        l_pid.addWidget(self.motor_selector, 0, 1)
        l_pid.addWidget(QtWidgets.QLabel("Kp:"), 1, 0)
        l_pid.addWidget(self.kp_sb, 1, 1)
        l_pid.addWidget(QtWidgets.QLabel("Ki:"), 2, 0)
        l_pid.addWidget(self.ki_sb, 2, 1)
        l_pid.addWidget(QtWidgets.QLabel("Kff:"), 3, 0)
        l_pid.addWidget(self.kff_sb, 3, 1)
        l_pid.addWidget(self.pid_update_btn, 4, 0, 1, 2)
        layout.addWidget(grp_pid)

        # --- Time Window Group ---
        grp_time = QtWidgets.QGroupBox("Time Window")
        l_time = QtWidgets.QGridLayout(grp_time)
        self.sample_period_edit = QtWidgets.QDoubleSpinBox()
        self.sample_period_edit.setRange(1, 1000)
        self.sample_period_edit.setValue(50.0)
        self.sample_period_edit.setSuffix(" ms")
        self.sample_count_edit = QtWidgets.QSpinBox()
        self.sample_count_edit.setRange(10, 10000)
        self.sample_count_edit.setValue(200)
        self.sample_period_edit.valueChanged.connect(self._emit_time_config)
        self.sample_count_edit.valueChanged.connect(self._emit_time_config)
        l_time.addWidget(QtWidgets.QLabel("Period:"), 0, 0)
        l_time.addWidget(self.sample_period_edit, 0, 1)
        l_time.addWidget(QtWidgets.QLabel("Samples:"), 1, 0)
        l_time.addWidget(self.sample_count_edit, 1, 1)
        layout.addWidget(grp_time)

        # --- Stream Selector ---
        grp_payload = QtWidgets.QGroupBox("Stream Type")
        l_payload = QtWidgets.QVBoxLayout(grp_payload)
        self.stream_loader = StreamConfigLoader("streams.json")
        self.payload_combo = QtWidgets.QComboBox()
        for sid, s in self.stream_loader.list_streams().items():
            self.payload_combo.addItem(s["name"], sid)
        self.payload_combo.currentIndexChanged.connect(self._on_stream_changed)
        l_payload.addWidget(self.payload_combo)
        layout.addWidget(grp_payload)

        # Apply Scales Button
        self.apply_btn = QtWidgets.QPushButton("Apply Scales")
        self.apply_btn.clicked.connect(self._apply_scales)
        layout.addWidget(self.apply_btn)

        # --- Signals List (Scrollable) ---
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.signals_container = QtWidgets.QWidget()
        self.signals_layout = QtWidgets.QVBoxLayout(self.signals_container)
        self.signals_layout.setSpacing(5)
        self.signals_layout.addStretch()
        scroll.setWidget(self.signals_container)
        grp_sigs = QtWidgets.QGroupBox("Signals")
        l_sigs = QtWidgets.QVBoxLayout(grp_sigs)
        l_sigs.addWidget(scroll)
        layout.addWidget(grp_sigs, 1)

        # Internal state
        self.y_controls = []
        self._accordion_groups = []

        # Initial population
        self.refresh_ports()

    def _make_sb(self, val):
        """Helper to create standard double spin boxes."""
        sb = QtWidgets.QDoubleSpinBox()
        sb.setRange(0, 1000)
        sb.setDecimals(4)
        sb.setValue(val)
        return sb

    def refresh_ports(self):
        """Refreshes the list of available COM ports and adds a VIRTUAL option."""
        self.port_combo.clear()
        self.port_combo.addItem("VIRTUAL", "VIRTUAL")
        for p in list_ports.comports():
            self.port_combo.addItem(f"{p.device}", p.device)

    def _on_pid_update(self):
        """Collects PID values and emits a configuration signal."""
        motor_id = self.motor_selector.currentData()
        self.pid_config_sent.emit(
            motor_id, self.kp_sb.value(), self.ki_sb.value(), self.kff_sb.value()
        )

    def _emit_time_config(self):
        """Emits a signal whenever time or sample count changes."""
        self.time_config_changed.emit(
            self.sample_period_edit.value(), self.sample_count_edit.value()
        )

    def _on_connect_toggled(self, checked):
        """Handles the Connect/Disconnect button toggle state."""
        if checked:
            self.connect_btn.setText("Disconnect")
            self.pause_btn.setEnabled(True)
            self.connection_requested.emit(
                self.port_combo.currentText(), int(self.baud_combo.currentText())
            )
        else:
            self.connect_btn.setText("Connect")
            self.pause_btn.setChecked(False)
            self.pause_btn.setEnabled(False)
            self.connection_requested.emit("STOP", 0)

    def _on_pause_toggled(self, checked):
        """Handles the Pause/Resume button toggle state."""
        self.pause_btn.setText("Resume" if checked else "Pause")
        self.pause_btn.setStyleSheet(
            "background-color: #F57F17; color: black; font-weight: bold;" if checked else ""
        )
        self.pause_requested.emit(checked)

    def _on_stream_changed(self, idx):
        """
        Handles selection of a different data stream type.

        Loads the new configuration and triggers the GUI rebuild for signals.
        """
        sid = self.payload_combo.itemData(idx)
        if not sid:
            return
        cfg = self.stream_loader.get_stream(sid)
        self._rebuild_signal_list(cfg)
        self.stream_changed.emit(cfg["signals"])

    def _rebuild_signal_list(self, cfg):
        """
        Dynamically rebuilds the list of signal controls based on configuration.

        It safely removes existing widgets, creates collapsible groups (accordion style),
        and populates them with Y-Axis control widgets.

        Args:
            cfg (dict): The stream configuration dictionary containing 'groups' and 'signals'.
        """
        # Safely remove old widgets
        while self.signals_layout.count() > 1:
            item = self.signals_layout.takeAt(0)
            if item is not None:
                w = item.widget()
                if w is not None:
                    w.deleteLater()
        self.y_controls = []
        self._accordion_groups = []
        groups = {}

        # Create Groups
        for gid, gdata in sorted(cfg["groups"].items(), key=lambda x: x[1]["order"]):
            grp = CollapsibleGroup(gdata["label"])
            grp.expanded.connect(self._on_group_expanded)
            self.signals_layout.insertWidget(self.signals_layout.count() - 1, grp)
            groups[gid] = grp
            self._accordion_groups.append(grp)

        # Create Signal Controls
        for sid, sdata in cfg["signals"].items():
            w = YAxisControlWidget(sdata["label"], sdata["color"])
            w.signal_id = sid
            w.min_edit.setValue(sdata["y_range"]["min"])
            w.max_edit.setValue(sdata["y_range"]["max"])
            w.enable_checkbox.toggled.connect(
                lambda c, s=sid: self.signal_visibility_changed.emit(s, c)
            )
            target = groups.get(sdata["group"])
            if target:
                target.content_layout.addWidget(w)
            self.y_controls.append(w)

        # Expand the first group by default
        if self._accordion_groups:
            self._accordion_groups[0].toggle.setChecked(True)

    def _on_group_expanded(self, sender):
        """Ensures only one accordion group is expanded at a time."""
        for g in self._accordion_groups:
            if g is not sender:
                g.toggle.setChecked(False)

    def _apply_scales(self):
        """Emits signals to update Y-axis ranges for all active signals."""
        for w in self.y_controls:
            self.scale_changed.emit(w.signal_id, w.min_edit.value(), w.max_edit.value())
