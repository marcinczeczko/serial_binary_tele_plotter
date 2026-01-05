"""
PID Tuning Panel Module.

This module provides a specialized widget for configuring PID controller parameters.
It encapsulates inputs for Proportional (Kp), Integral (Ki), and Feed-Forward (Kff)
gains, as well as setpoint generation settings (Ramp/Step, Target Velocity).
"""

from PyQt6 import QtCore, QtWidgets


class PidTuningPanel(QtWidgets.QGroupBox):
    """
    A group box widget containing controls for real-time PID tuning.

    It collects parameters from the UI and emits a unified signal to update
    the controller state on the MCU.

    Attributes:
        pid_config_sent (pyqtSignal): Emitted when the update button is clicked.
            Payload signature: (ramp_type, motor_id, kp, ki, kff, alpha, rps).
        motor_changed (pyqtSignal): Emitted when the motor selection combo box changes.
            Payload: motor_id (int).
    """

    # Signal Payload Definition:
    # 1. ramp_type (int): 0 = Step Input, 1 = Ramp Input
    # 2. motor_id (int): 0 = Left, 1 = Right
    # 3. kp (float): Proportional Gain
    # 4. ki (float): Integral Gain
    # 5. kff (float): Feed-Forward Gain
    # 6. alpha (float): Derivative Filter / Low-Pass Filter Coefficient
    # 7. rps (float): Target Rotations Per Second
    pid_config_sent = QtCore.pyqtSignal(int, int, float, float, float, float, float)

    # Signal for local UI synchronization (e.g., changing plot visibility based on motor)
    motor_changed = QtCore.pyqtSignal(int)

    def __init__(self):
        """Initializes the PID tuning layout and widgets."""
        super().__init__("PID Tuning")

        layout = QtWidgets.QGridLayout(self)
        layout.setSpacing(8)

        # --- Motor Selector ---
        self.motor_selector = QtWidgets.QComboBox()
        self.motor_selector.addItem("Left Motor", 0)
        self.motor_selector.addItem("Right Motor", 1)
        self.motor_selector.addItem("Both Motors", 2)
        self.motor_selector.setToolTip("Select which motor to tune")
        self.motor_selector.currentIndexChanged.connect(self._on_motor_changed)

        # --- Tuning Parameters ---
        # We use a helper method to ensure consistent styling and ranges
        self.kp_sb = self._make_sb(1.0, "Proportional Gain")
        self.ki_sb = self._make_sb(0.0, "Integral Gain")
        self.kff_sb = self._make_sb(0.0, "Feed-Forward Gain")

        # Alpha usually represents a filter coefficient (0.0 - 1.0) or similar factor
        self.alpha_sb = self._make_sb(0.5, "Filter Coefficient (Alpha)")

        # Target Velocity
        self.rps_sb = self._make_sb(0.5, "Target Velocity (RPS)")

        # Input Type Selector
        self.ramp_cb = QtWidgets.QCheckBox()
        self.ramp_cb.setToolTip("Checked = Ramp Input, Unchecked = Step Input")

        # --- Update Action ---
        self.update_btn = QtWidgets.QPushButton("Update PI and Run")
        self.update_btn.setToolTip("Send parameters to MCU and trigger motion")
        self.update_btn.clicked.connect(self._on_update_clicked)

        # --- Layout Assembly ---
        # Row 0: Target Selection
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

        # Row 5: Setpoint (RPS)
        layout.addWidget(QtWidgets.QLabel("Rps:"), 5, 0)
        layout.addWidget(self.rps_sb, 5, 1)

        # Row 6: Input Type
        layout.addWidget(QtWidgets.QLabel("Ramp:"), 6, 0)
        layout.addWidget(self.ramp_cb, 6, 1)

        # Row 7: Action Button (Spanning 2 columns)
        layout.addWidget(self.update_btn, 7, 0, 1, 2)

    def _make_sb(self, val: float, tooltip: str = "") -> QtWidgets.QDoubleSpinBox:
        """
        Factory method for creating consistent QDoubleSpinBox widgets.

        Args:
            val (float): Initial value.
            tooltip (str): Tooltip text for UI guidance.

        Returns:
            QtWidgets.QDoubleSpinBox: Configured spinbox widget.
        """
        sb = QtWidgets.QDoubleSpinBox()
        sb.setRange(0.0, 1000.0)
        sb.setDecimals(2)
        sb.setSingleStep(0.1)
        sb.setValue(val)
        if tooltip:
            sb.setToolTip(tooltip)
        return sb

    def _on_motor_changed(self, index: int) -> None:
        """
        Slot handling motor selection changes.

        Args:
            index (int): The new index of the combo box (unused, we fetch data).
        """
        # Retrieve the user data (motor ID) associated with the item
        motor_id = int(self.motor_selector.currentData())
        self.motor_changed.emit(motor_id)

    def _on_update_clicked(self) -> None:
        """
        Collects values from all widgets and emits the configuration signal.
        This triggers the packet transmission in the Engine.
        """
        motor_id = int(self.motor_selector.currentData())

        # Convert Checkbox state to Integer (0 or 1) for binary protocol
        ramp_type = 1 if self.ramp_cb.isChecked() else 0

        kp = self.kp_sb.value()
        ki = self.ki_sb.value()
        kff = self.kff_sb.value()
        alpha = self.alpha_sb.value()
        rps = self.rps_sb.value()

        self.pid_config_sent.emit(ramp_type, motor_id, kp, ki, kff, alpha, rps)
