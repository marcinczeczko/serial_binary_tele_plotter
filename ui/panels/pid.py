from PyQt6 import QtCore, QtWidgets


class PidTuningPanel(QtWidgets.QWidget):
    """
    Pure content widget for PID tuning.
    Collapsing/expanding is handled EXTERNALLY by CollapsableSection.
    """

    pid_left_sent = QtCore.pyqtSignal(int, float, float, float, float, float)
    pid_right_sent = QtCore.pyqtSignal(int, float, float, float, float, float)
    run_test_sent = QtCore.pyqtSignal(float, float)

    def __init__(self):
        super().__init__()

        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(8, 6, 8, 6)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)

        # ===== Header Row =====
        grid.addWidget(QtWidgets.QLabel(""), 0, 0)
        grid.addWidget(QtWidgets.QLabel("<b>Left</b>"), 0, 1)
        grid.addWidget(QtWidgets.QLabel("<b>Right</b>"), 0, 2)

        self.left = {}
        self.right = {}

        params = [
            ("Kp", 1.0),
            ("Ki", 0.0),
            ("Kff", 0.0),
            ("Alpha", 0.5),
            ("Rps", 0.5),
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
        grid.addWidget(QtWidgets.QLabel("Ramp:"), row, 0)
        self.left["Ramp"] = QtWidgets.QCheckBox()
        self.right["Ramp"] = QtWidgets.QCheckBox()
        grid.addWidget(self.left["Ramp"], row, 1)
        grid.addWidget(self.right["Ramp"], row, 2)
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

    def _emit_left(self):
        self.pid_left_sent.emit(
            int(self.left["Ramp"].isChecked()),
            self.left["Kp"].value(),
            self.left["Ki"].value(),
            self.left["Kff"].value(),
            self.left["Alpha"].value(),
            self.left["Rps"].value(),
        )

    def _emit_right(self):
        self.pid_right_sent.emit(
            int(self.right["Ramp"].isChecked()),
            self.right["Kp"].value(),
            self.right["Ki"].value(),
            self.right["Kff"].value(),
            self.right["Alpha"].value(),
            self.right["Rps"].value(),
        )

    def _emit_run_test(self):
        self.run_test_sent.emit(self.left["Rps"].value(), self.right["Rps"].value())
