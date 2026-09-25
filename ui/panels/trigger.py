"""
Trigger & step response (R4.4, R4.5, R6.5), in two parts.

- `setup`: which signal, edge and level, and how much to keep before and after the
  crossing, plus Arm. It opens from the top bar's `T`. While armed, the level
  is also a dashed line on the plot that can be dragged.
- `results`: which signals are the setpoint and the measurement, and each capture's rise
  time, overshoot, settling time and steady-state error, next to the previous capture's,
  whose traces can be overlaid (e.g. before and after a gain change). It's the right
  pane's Step view: a Now / Prev / Δ table, Now in the measurement's colour, Prev dim,
  the change green when it improved (R9.5).

A capture freezes in analysis mode with the Δ anchor at the trigger.
"""

from __future__ import annotations

import math

from PyQt6 import QtCore, QtGui, QtWidgets

from core.analysis.step_response import SETTLE_BAND, StepMetrics
from core.analysis.trigger import EDGES, TriggerSpec
from core.types import StreamConfig
from styles import BORDER, TEXT, TEXT_DIM, TEXT_MUTED, mono_font
from ui.common.numbers import ScopeDoubleSpinBox, format_number

EDGE_ARROWS = {"rising": "↗", "falling": "↘", "either": "↕"}
EDGE_OPS = {"rising": ">", "falling": "<", "either": "×"}
BETTER = "#3DFF6E"
WORSE = "#FFB000"
NO_CAPTURE = "Arm T in the top bar to capture a step."
METRIC_ROWS = (
    ("Rise time 10–90 %", "rise"),
    ("Overshoot", "overshoot"),
    (f"Settling ±{SETTLE_BAND * 100:.0f} %", "settling"),
    ("Steady-state error", "sse"),
)


class TriggerPanel(QtCore.QObject):
    arm_requested = QtCore.pyqtSignal(object)  # TriggerSpec
    disarm_requested = QtCore.pyqtSignal()
    changed = QtCore.pyqtSignal()  # the trigger settings changed (for the level line)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._labels: dict[str, str] = {}
        self._state = "idle"

        # --- setup (toolbar popup) ---
        self.setup = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(self.setup)
        form.setContentsMargins(10, 10, 10, 10)
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
        form.addRow("Signal:", self.signal_combo)
        form.addRow("Edge:", self.edge_combo)
        form.addRow("Level:", self.level_sb)
        form.addRow("Before:", self.pre_sb)
        form.addRow("After:", self.post_sb)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.arm_btn)
        row.addWidget(self.state_lbl, 1)
        form.addRow(row)
        for signal in (
            self.signal_combo.currentIndexChanged,
            self.edge_combo.currentIndexChanged,
            self.level_sb.valueChanged,
        ):
            signal.connect(self.changed)

        # --- results (the right pane's Step view) ---
        self.results = QtWidgets.QWidget()
        rlayout = QtWidgets.QVBoxLayout(self.results)
        rlayout.setContentsMargins(8, 8, 8, 8)
        rlayout.setSpacing(8)
        self.metrics_lbl = QtWidgets.QLabel(NO_CAPTURE)
        self.metrics_lbl.setWordWrap(True)
        self.metrics_lbl.setStyleSheet(f"color: {TEXT_DIM};")
        rlayout.addWidget(self.metrics_lbl)
        self.metrics_table = QtWidgets.QTableWidget(len(METRIC_ROWS), 3)
        self.metrics_table.setHorizontalHeaderLabels(["Now", "Prev", "Δ"])
        self.metrics_table.setVerticalHeaderLabels([label for label, _ in METRIC_ROWS])
        self.metrics_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.metrics_table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ContiguousSelection
        )
        self.metrics_table.setShowGrid(False)
        self.metrics_table.setStyleSheet(
            "QTableWidget { border: none; background: transparent; }"
            f" QHeaderView::section {{ background: transparent; color: {TEXT_MUTED};"
            f" border: none; border-bottom: 1px solid {BORDER}; padding: 3px 6px; }}"
            f" QHeaderView::section:vertical {{ color: {TEXT_DIM}; border-bottom: none; }}"
        )
        header = self.metrics_table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        vheader = self.metrics_table.verticalHeader()
        assert vheader is not None
        vheader.setDefaultAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self.metrics_table.setFixedHeight(
            (vheader.defaultSectionSize() * len(METRIC_ROWS)) + header.sizeHint().height() + 4
        )
        rlayout.addWidget(self.metrics_table)
        pick = QtWidgets.QFormLayout()
        self.setpoint_combo = QtWidgets.QComboBox()
        self.measurement_combo = QtWidgets.QComboBox()
        pick.addRow("Setpoint", self.setpoint_combo)
        pick.addRow("Measured", self.measurement_combo)
        rlayout.addLayout(pick)
        self.overlay_chk = QtWidgets.QCheckBox("Overlay previous")
        self.overlay_chk.setChecked(True)
        rlayout.addWidget(self.overlay_chk)
        rlayout.addStretch()
        self._colors: dict[str, str] = {}

    # --- configuration ---

    def set_signals(self, cfg: StreamConfig) -> None:
        """Offers the stream's signals; picks likely defaults (the user can change them)."""
        signals = cfg.get("signals", {})
        self._labels = {sid: sig.get("label", sid) for sid, sig in signals.items()}
        self._colors = {sid: str(sig.get("color", TEXT)) for sid, sig in signals.items()}
        for combo in (self.signal_combo, self.setpoint_combo, self.measurement_combo):
            combo.blockSignals(True)
            combo.clear()
            for sid, label in self._labels.items():
                combo.addItem(label, sid)
            combo.blockSignals(False)
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
        self.metrics_lbl.setText(NO_CAPTURE)
        self.metrics_table.clearContents()
        self.changed.emit()

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

    def set_level(self, level: float) -> None:
        """The level line was dragged on the plot."""
        self.level_sb.setValue(level)

    # --- state ---

    @property
    def state(self) -> str:
        return self._state

    def set_armed(self, armed: bool) -> None:
        """Reflects the controller's state without emitting (e.g. after a capture)."""
        self.arm_btn.blockSignals(True)
        self.arm_btn.setChecked(armed)
        self.arm_btn.setText("Disarm" if armed else "Arm")
        self.arm_btn.blockSignals(False)

    def show_state(self, state: str) -> None:
        self._state = state if state in ("armed", "fired") else "idle"
        self.state_lbl.setText(
            {"armed": "Armed: waiting…", "fired": "Triggered: capturing…"}.get(state, "Idle")
        )
        self.set_armed(state in ("armed", "fired"))
        self.changed.emit()

    def summary(self) -> str:
        """For the toolbar button: what the trigger waits for, e.g. "↘ L target < 0.15"."""
        spec = self.spec()
        if spec is None:
            return "Trigger"
        label = self._labels.get(spec.signal, spec.signal)
        return f"{EDGE_ARROWS[spec.edge]} {label} {EDGE_OPS[spec.edge]} {spec.level:g}"

    # --- results ---

    def show_metrics(self, current: StepMetrics | None, previous: StepMetrics | None) -> None:
        pair = self.step_signals()
        names = (
            f"{self._labels.get(pair[0], pair[0])} → {self._labels.get(pair[1], pair[1])}"
            if pair
            else ""
        )
        if current is None:
            self.metrics_lbl.setText(f"{names}: no step found in this capture")
        else:
            self.metrics_lbl.setText(
                f"{names} · step {current.initial:+.4g} → {current.final:+.4g}"
            )
        measured = self.measurement_combo.currentData()
        now_color = self._colors.get(measured, TEXT) if isinstance(measured, str) else TEXT
        for row, (_, key) in enumerate(METRIC_ROWS):
            now = _metric(current, key)
            before = _metric(previous, key)
            self._set_cell(row, 0, _format(key, now), now_color)
            self._set_cell(row, 1, _format(key, before) if previous is not None else "", TEXT_MUTED)
            text, color = _change(key, now, before)
            self._set_cell(row, 2, text if previous is not None else "", color)

    def metric_text(self, row: int, column: int) -> str:
        item = self.metrics_table.item(row, column)
        return item.text() if item is not None else ""

    def _set_cell(self, row: int, column: int, text: str, color: str | None = None) -> None:
        item = QtWidgets.QTableWidgetItem(text)
        item.setFont(mono_font())
        if color is not None:
            item.setForeground(QtGui.QColor(color))
        self.metrics_table.setItem(row, column, item)

    def _on_arm_toggled(self, checked: bool) -> None:
        spec = self.spec()
        if checked and spec is not None:
            self.arm_btn.setText("Disarm")
            self.arm_requested.emit(spec)
        else:
            self.arm_btn.setText("Arm")
            self.disarm_requested.emit()


def _metric(metrics: StepMetrics | None, key: str) -> float | None:
    if metrics is None:
        return None
    return {
        "rise": metrics.rise_time_s,
        "overshoot": metrics.overshoot_pct,
        "settling": metrics.settling_time_s,
        "sse": metrics.steady_state_error,
    }[key]


def _format(key: str, value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "n/a"
    if key in ("rise", "settling"):
        return f"{value:.3f} s"
    if key == "overshoot":
        return f"{value:.1f} %"
    return f"{value:+.4g}"


def _change(key: str, now: float | None, before: float | None) -> tuple[str, str | None]:
    """The change from the previous capture; for every metric, smaller is better."""
    if now is None or before is None or not (math.isfinite(now) and math.isfinite(before)):
        return "", None
    if key == "sse":
        now, before = abs(now), abs(before)
    if abs(now - before) < 1e-12:
        return "same", None
    color = BETTER if now < before else WORSE
    arrow = "▼" if now < before else "▲"
    if key == "overshoot":
        return f"{arrow} {format_number(now - before, 1, sign=True)} pt", color
    if before == 0:
        return f"{arrow} {now - before:+.3g}", color
    return f"{arrow} {(now - before) / before * 100:+.0f} %", color


def _spin(
    value: float, lo: float, hi: float, decimals: int = 3, suffix: str = ""
) -> QtWidgets.QDoubleSpinBox:
    sb = ScopeDoubleSpinBox()
    sb.setRange(lo, hi)
    sb.setDecimals(decimals)
    sb.setValue(value)
    sb.setSuffix(suffix)
    sb.setKeyboardTracking(False)
    return sb
