"""
Trigger & step-response panel (R4.4, R4.5).

Trigger: which signal, level, edge, and how much to keep before and after the crossing.
Arming waits for the next crossing on the live data, then freezes that capture in
analysis mode (Δ anchored at the trigger).

Step response: which signal is the setpoint and which the measurement. Each capture's rise
time, overshoot, settling time and steady-state error are shown next to the previous
capture's, whose traces can be overlaid (for example before and after a gain change).
"""

from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from core.analysis.step_response import StepMetrics
from core.analysis.trigger import EDGES, TriggerSpec
from core.types import StreamConfig


class TriggerPanel(QtWidgets.QWidget):
    arm_requested = QtCore.pyqtSignal(object)  # TriggerSpec
    disarm_requested = QtCore.pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        form = QtWidgets.QFormLayout(self)
        form.setContentsMargins(4, 4, 4, 4)

        self.signal_combo = QtWidgets.QComboBox()
        self.edge_combo = QtWidgets.QComboBox()
        self.edge_combo.addItems(EDGES)
        self.level_sb = _spin(0.0, -1e9, 1e9, decimals=4)
        self.pre_sb = _spin(0.5, 0.0, 3600.0, decimals=3, suffix=" s")
        self.post_sb = _spin(2.0, 0.001, 3600.0, decimals=3, suffix=" s")
        self.arm_btn = QtWidgets.QPushButton("Arm")
        self.arm_btn.setCheckable(True)
        self.arm_btn.toggled.connect(self._on_arm_toggled)
        self.state_lbl = QtWidgets.QLabel("Idle")

        self.setpoint_combo = QtWidgets.QComboBox()
        self.measurement_combo = QtWidgets.QComboBox()
        self.overlay_chk = QtWidgets.QCheckBox("Overlay the previous capture")
        self.overlay_chk.setChecked(True)
        self.metrics_lbl = QtWidgets.QLabel("")
        self.metrics_lbl.setWordWrap(True)
        self.metrics_lbl.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )

        form.addRow("Signal:", self.signal_combo)
        form.addRow("Edge:", self.edge_combo)
        form.addRow("Level:", self.level_sb)
        form.addRow("Before:", self.pre_sb)
        form.addRow("After:", self.post_sb)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.arm_btn)
        row.addWidget(self.state_lbl, 1)
        form.addRow(row)
        form.addRow(QtWidgets.QLabel("<b>Step response</b>"))
        form.addRow("Setpoint:", self.setpoint_combo)
        form.addRow("Measurement:", self.measurement_combo)
        form.addRow(self.overlay_chk)
        form.addRow(self.metrics_lbl)

    def set_signals(self, cfg: StreamConfig) -> None:
        """Offers the stream's signals; picks likely defaults (the user can change them)."""
        signals = cfg.get("signals", {})
        for combo in (self.signal_combo, self.setpoint_combo, self.measurement_combo):
            combo.clear()
            for sid, sig in signals.items():
                combo.addItem(sig.get("label", sid), sid)
        ids = list(signals)
        setpoint: str | None = next(
            (s for s in ids if "setpoint" in s.lower()), ids[0] if ids else None
        )
        measured: str | None = next(
            (s for s in ids if "measurement" in s.lower()), ids[1] if len(ids) > 1 else setpoint
        )
        for combo, choice in (
            (self.signal_combo, setpoint),
            (self.setpoint_combo, setpoint),
            (self.measurement_combo, measured),
        ):
            if choice is not None:
                combo.setCurrentIndex(combo.findData(choice))
        self.set_armed(False)
        self.metrics_lbl.setText("")

    def spec(self) -> TriggerSpec | None:
        sid = self.signal_combo.currentData()
        if not isinstance(sid, str):
            return None
        return TriggerSpec(
            signal=sid,
            level=self.level_sb.value(),
            edge=self.edge_combo.currentText(),
            pre_s=self.pre_sb.value(),
            post_s=self.post_sb.value(),
        )

    def step_signals(self) -> tuple[str, str] | None:
        sp, meas = self.setpoint_combo.currentData(), self.measurement_combo.currentData()
        return (sp, meas) if isinstance(sp, str) and isinstance(meas, str) else None

    def set_armed(self, armed: bool) -> None:
        """Reflects the controller's state without emitting (e.g. after a capture)."""
        self.arm_btn.blockSignals(True)
        self.arm_btn.setChecked(armed)
        self.arm_btn.setText("Disarm" if armed else "Arm")
        self.arm_btn.blockSignals(False)

    def show_state(self, state: str) -> None:
        self.state_lbl.setText(
            {"armed": "Armed: waiting…", "fired": "Triggered: capturing…"}.get(state, "Idle")
        )
        self.set_armed(state in ("armed", "fired"))

    def show_metrics(self, current: StepMetrics | None, previous: StepMetrics | None) -> None:
        lines = [f"<b>This capture:</b> {current.summary() if current else 'no step found'}"]
        if previous is not None:
            lines.append(f"<b>Previous:</b> {previous.summary()}")
        self.metrics_lbl.setText("<br>".join(lines))

    def _on_arm_toggled(self, checked: bool) -> None:
        spec = self.spec()
        if checked and spec is not None:
            self.arm_btn.setText("Disarm")
            self.arm_requested.emit(spec)
        else:
            self.arm_btn.setText("Arm")
            self.disarm_requested.emit()


def _spin(
    value: float, lo: float, hi: float, decimals: int = 3, suffix: str = ""
) -> QtWidgets.QDoubleSpinBox:
    sb = QtWidgets.QDoubleSpinBox()
    sb.setRange(lo, hi)
    sb.setDecimals(decimals)
    sb.setValue(value)
    sb.setSuffix(suffix)
    sb.setKeyboardTracking(False)
    return sb
