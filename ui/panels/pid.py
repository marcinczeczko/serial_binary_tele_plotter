from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class ParamSpec:
    default: float
    minimum: float = -1000.0
    maximum: float = 1000.0
    decimals: int = 4
    step: float = 0.01


# Signed ranges: a negative Rps is reverse, and a sign-flipped gain is a valid experiment
# (C8). Moving these into streams.json command definitions is roadmap R5.2.
PARAM_SPECS: dict[str, ParamSpec] = {
    PARAM_KP: ParamSpec(0.1),
    PARAM_KI: ParamSpec(0.02, decimals=5, step=0.001),
    PARAM_K1: ParamSpec(26.5, step=0.1),
    PARAM_K2: ParamSpec(8.0, step=0.1),
    PARAM_K3: ParamSpec(5.0, step=0.1),
    PARAM_KAW: ParamSpec(1.0),
    PARAM_ALPHA: ParamSpec(0.2, minimum=0.0, maximum=1.0),
    PARAM_RPS: ParamSpec(0.3, minimum=-50.0, maximum=50.0),
}


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

        row = 1
        for name, spec in PARAM_SPECS.items():
            grid.addWidget(QtWidgets.QLabel(f"{name}:"), row, 0)
            self.left[name] = self._sb(spec)
            self.right[name] = self._sb(spec)
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
    def _sb(self, spec: ParamSpec) -> QtWidgets.QDoubleSpinBox:
        sb = QtWidgets.QDoubleSpinBox()
        sb.setRange(spec.minimum, spec.maximum)
        sb.setDecimals(spec.decimals)
        sb.setSingleStep(spec.step)
        sb.setValue(spec.default)
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
