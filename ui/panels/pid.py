from __future__ import annotations

from typing import cast

from PyQt6 import QtCore, QtWidgets

# Float params
PARAM_KP = "Kp"
PARAM_KI = "Ki"
PARAM_K1 = "K1"
PARAM_K2 = "K2"
PARAM_K3 = "K3"
PARAM_KAW = "Kaw"
PARAM_ALPHA = "Alpha"
PARAM_RPS = "Rps"

# Checkbox params
PARAM_USE_RAMP = "useRamp"
PARAM_USE_PI = "usePI"


class PidTuningPanel(QtWidgets.QWidget):
    """
    Pure content widget for PID tuning.
    Collapsing/expanding is handled EXTERNALLY by CollapsableSection.
    """

    pid_left_sent = QtCore.pyqtSignal(
        int, int, float, float, float, float, float, float, float, float
    )
    pid_right_sent = QtCore.pyqtSignal(
        int, int, float, float, float, float, float, float, float, float
    )
    run_test_sent = QtCore.pyqtSignal(
        int,
        int,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
        int,
        int,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
    )

    def __init__(self) -> None:
        super().__init__()

        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(8, 6, 8, 6)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)

        # ===== Header Row =====
        grid.addWidget(QtWidgets.QLabel(""), 0, 0)
        grid.addWidget(QtWidgets.QLabel("<b>Left</b>"), 0, 1)
        grid.addWidget(QtWidgets.QLabel("<b>Right</b>"), 0, 2)

        self.left: dict[str, QtWidgets.QDoubleSpinBox | QtWidgets.QCheckBox] = {}
        self.right: dict[str, QtWidgets.QDoubleSpinBox | QtWidgets.QCheckBox] = {}

        params = [
            (PARAM_KP, 0.1),
            (PARAM_KI, 0.02),
            (PARAM_K1, 26.5),
            (PARAM_K2, 8.0),
            (PARAM_K3, 5.0),
            (PARAM_KAW, 1.0),
            (PARAM_ALPHA, 0.2),
            (PARAM_RPS, 0.3),
        ]

        row = 1
        for name, val in params:
            grid.addWidget(QtWidgets.QLabel(f"{name}:"), row, 0)
            self.left[name] = self._sb(val)
            self.right[name] = self._sb(val)
            grid.addWidget(self.left[name], row, 1)
            grid.addWidget(self.right[name], row, 2)
            row += 1

        # ===== Ramp =====
        grid.addWidget(QtWidgets.QLabel("Use Ramp:"), row, 0)
        self.left[PARAM_USE_RAMP] = QtWidgets.QCheckBox()
        self.right[PARAM_USE_RAMP] = QtWidgets.QCheckBox()
        grid.addWidget(self.left[PARAM_USE_RAMP], row, 1)
        grid.addWidget(self.right[PARAM_USE_RAMP], row, 2)
        row += 1

        # ===== Use PI =====
        grid.addWidget(QtWidgets.QLabel("Use PI:"), row, 0)
        self.left[PARAM_USE_PI] = QtWidgets.QCheckBox()
        self.right[PARAM_USE_PI] = QtWidgets.QCheckBox()
        grid.addWidget(self.left[PARAM_USE_PI], row, 1)
        grid.addWidget(self.right[PARAM_USE_PI], row, 2)
        row += 1

        # ===== Update buttons =====
        btn_l = QtWidgets.QPushButton("Update Left PID")
        btn_r = QtWidgets.QPushButton("Update Right PID")
        btn_l.clicked.connect(self._emit_left)
        btn_r.clicked.connect(self._emit_right)

        grid.addWidget(btn_l, row, 1)
        grid.addWidget(btn_r, row, 2)
        row += 1

        # ===== Run test =====
        run_btn = QtWidgets.QPushButton("Run Test (Both Motors)")
        run_btn.clicked.connect(self._emit_run_test)
        grid.addWidget(run_btn, row, 0, 1, 3)

    # ======================================================
    # Helpers
    # ======================================================
    def _sb(self, val: float) -> QtWidgets.QDoubleSpinBox:
        sb = QtWidgets.QDoubleSpinBox()
        sb.setRange(0.0, 1000.0)
        sb.setDecimals(3)
        sb.setSingleStep(0.1)
        sb.setValue(val)
        return sb

    def _emit_left(self) -> None:
        self.pid_left_sent.emit(
            int(cast(QtWidgets.QCheckBox, self.left[PARAM_USE_RAMP]).isChecked()),
            int(cast(QtWidgets.QCheckBox, self.left[PARAM_USE_PI]).isChecked()),
            cast(QtWidgets.QDoubleSpinBox, self.left[PARAM_KP]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.left[PARAM_KI]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.left[PARAM_K1]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.left[PARAM_K2]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.left[PARAM_K3]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.left[PARAM_KAW]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.left[PARAM_ALPHA]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.left[PARAM_RPS]).value(),
        )

    def _emit_right(self) -> None:
        self.pid_right_sent.emit(
            int(cast(QtWidgets.QCheckBox, self.right[PARAM_USE_RAMP]).isChecked()),
            int(cast(QtWidgets.QCheckBox, self.right[PARAM_USE_PI]).isChecked()),
            cast(QtWidgets.QDoubleSpinBox, self.right[PARAM_KP]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.right[PARAM_KI]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.right[PARAM_K1]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.right[PARAM_K2]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.right[PARAM_K3]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.right[PARAM_KAW]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.right[PARAM_ALPHA]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.right[PARAM_RPS]).value(),
        )

    def _emit_run_test(self) -> None:
        self.run_test_sent.emit(
            int(cast(QtWidgets.QCheckBox, self.left[PARAM_USE_RAMP]).isChecked()),
            int(cast(QtWidgets.QCheckBox, self.left[PARAM_USE_PI]).isChecked()),
            cast(QtWidgets.QDoubleSpinBox, self.left[PARAM_KP]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.left[PARAM_KI]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.left[PARAM_K1]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.left[PARAM_K2]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.left[PARAM_K3]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.left[PARAM_KAW]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.left[PARAM_ALPHA]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.left[PARAM_RPS]).value(),
            int(cast(QtWidgets.QCheckBox, self.right[PARAM_USE_RAMP]).isChecked()),
            int(cast(QtWidgets.QCheckBox, self.right[PARAM_USE_PI]).isChecked()),
            cast(QtWidgets.QDoubleSpinBox, self.right[PARAM_KP]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.right[PARAM_KI]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.right[PARAM_K1]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.right[PARAM_K2]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.right[PARAM_K3]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.right[PARAM_KAW]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.right[PARAM_ALPHA]).value(),
            cast(QtWidgets.QDoubleSpinBox, self.right[PARAM_RPS]).value(),
        )
