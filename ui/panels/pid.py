"""
PID Tuning Panel Module.

This module provides a specialized widget for configuring PID controller parameters.
It encapsulates the inputs for proportional, integral, and derivative gains,
as well as feed-forward and filtering settings.
"""

from PyQt6 import QtCore, QtWidgets


class PidTuningPanel(QtWidgets.QGroupBox):
    """
    A group box widget containing controls for real-time PID tuning.

    Attributes:
        pid_config_sent (pyqtSignal): Emitted when the update button is clicked.
            Payload: (ramp_type, motor_id, kp, ki, kff, alpha, rps)
    """

    # Signal Payload: (ramp_type: int, motor_id: int, kp: float, ki: float, kff: float, alpha: float, rps: float)
    pid_config_sent = QtCore.pyqtSignal(int, int, float, float, float, float, float)

    # Optional: If you want to notify about local motor selection change, though usually only needed on 'Update'
    motor_changed = QtCore.pyqtSignal(int)

    def __init__(self):
        """Initializes the PID tuning layout and widgets."""
        super().__init__("PID Tuning")

        layout = QtWidgets.QGridLayout(self)
        layout.setSpacing(8)

        # Motor Selector (Target for PID config)
        self.motor_selector = QtWidgets.QComboBox()
        self.motor_selector.addItem("Left Motor", 0)
        self.motor_selector.addItem("Right Motor", 1)
        self.motor_selector.currentIndexChanged.connect(self._on_motor_changed)

        # Tuning Parameters
        self.kp_sb = self._make_sb(1.0)
        self.ki_sb = self._make_sb(0.0)
        self.kff_sb = self._make_sb(0.0)
        self.alpha_sb = self._make_sb(0.5)
        self.rps_sb = self._make_sb(0.5)
        self.ramp_cb = QtWidgets.QCheckBox()

        # Update Button
        self.update_btn = QtWidgets.QPushButton("Update PI and Run")
        self.update_btn.clicked.connect(self._on_update_clicked)

        # Layout Assembly
        # Row 0: Motor Selection
        layout.addWidget(QtWidgets.QLabel("Motor:"), 0, 0)
        layout.addWidget(self.motor_selector, 0, 1)

        # Row 1: Kp
        layout.addWidget(QtWidgets.QLabel("Kp:"), 1, 0)
        layout.addWidget(self.kp_sb, 1, 1)

        # Row 2: Ki
        layout.addWidget(QtWidgets.QLabel("Ki:"), 2, 0)
        layout.addWidget(self.ki_sb, 2, 1)

        # Row 3: Kff
        layout.addWidget(QtWidgets.QLabel("Kff:"), 3, 0)
        layout.addWidget(self.kff_sb, 3, 1)

        # Row 4: Alpha
        layout.addWidget(QtWidgets.QLabel("Alpha:"), 4, 0)
        layout.addWidget(self.alpha_sb, 4, 1)

        # Row 5: RPS
        layout.addWidget(QtWidgets.QLabel("Rps:"), 5, 0)
        layout.addWidget(self.rps_sb, 5, 1)

        # Row 6: Ramp
        layout.addWidget(QtWidgets.QLabel("Ramp:"), 6, 0)
        layout.addWidget(self.ramp_cb, 6, 1)

        # Row 7: Button
        layout.addWidget(self.update_btn, 7, 0, 1, 2)

    def _make_sb(self, val: float) -> QtWidgets.QDoubleSpinBox:
        """Helper to create standard double spin boxes with consistent styling."""
        sb = QtWidgets.QDoubleSpinBox()
        sb.setRange(0.0, 1000.0)
        sb.setDecimals(2)
        sb.setSingleStep(0.1)
        sb.setValue(val)
        return sb

    def _on_motor_changed(self, index: int):
        """Emits signal when the target motor for PID config changes."""
        motor_id = self.motor_selector.currentData()
        self.motor_changed.emit(motor_id)

    def _on_update_clicked(self):
        """Collects values from widgets and emits the configuration signal."""
        motor_id = self.motor_selector.currentData()

        ramp_type = 1 if self.ramp_cb.isChecked() else 0
        kp = self.kp_sb.value()
        ki = self.ki_sb.value()
        kff = self.kff_sb.value()
        alpha = self.alpha_sb.value()
        rps = self.rps_sb.value()

        self.pid_config_sent.emit(ramp_type, motor_id, kp, ki, kff, alpha, rps)
