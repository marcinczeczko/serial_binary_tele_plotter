"""
Signal List Panel Module.

A flat list of the shown stream's signals: visibility, and the lane each one is drawn in
(R3.1). Lane moves made here last for the session; the Configuration tab's Lane column
(`signals[*].group`) saves them.
"""

from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from core.types import StreamConfig
from ui.charts.lanes import lane_layout
from ui.common.widgets import YAxisControlWidget

NEW_LANE = "__new__"
DEFAULT_LANE_LABEL = "Main"


class SignalListPanel(QtWidgets.QWidget):
    """
    Manages a flat list of signals and their visibility and lane controls.
    """

    signal_visibility_changed = QtCore.pyqtSignal(str, bool)
    signal_lane_changed = QtCore.pyqtSignal(str, str, str)  # signal id, lane key, lane label

    def __init__(self) -> None:
        super().__init__()

        # --- Main Layout ---
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        grp_sigs = QtWidgets.QGroupBox("Signals Visibility")
        l_sigs = QtWidgets.QVBoxLayout(grp_sigs)

        # --- Scroll Area ---
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        self.signals_container = QtWidgets.QWidget()
        self.signals_layout = QtWidgets.QVBoxLayout(self.signals_container)
        self.signals_layout.setSpacing(2)  # tighter spacing for the flat list
        self.signals_layout.addStretch()

        scroll.setWidget(self.signals_container)
        l_sigs.addWidget(scroll)
        layout.addWidget(grp_sigs)

        self.rows: dict[str, YAxisControlWidget] = {}
        self._lanes: list[tuple[str, str]] = []  # (key, label), in display order

    def rebuild_list(self, cfg: StreamConfig) -> None:
        """Rebuilds the list for a stream: its signals, and its lanes as choices."""
        while self.signals_layout.count() > 1:
            item = self.signals_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.hide()  # immediately: deleteLater only runs from the event loop
                widget.deleteLater()
        self.rows = {}

        specs, assignment = lane_layout(cfg)
        self._lanes = [(s.key, s.label or DEFAULT_LANE_LABEL) for s in specs]

        signals = cfg.get("signals", {})
        for sid, sdata in signals.items():
            w = YAxisControlWidget(
                sdata.get("label", sid),
                sdata.get("color", "#FFFFFF"),
                sdata.get("visible", True),
                with_lane=True,
            )
            w.enable_checkbox.toggled.connect(
                lambda checked, captured_sid=sid: self.signal_visibility_changed.emit(
                    captured_sid, checked
                )
            )
            self.rows[sid] = w
            self._fill_lane_combo(sid, assignment[sid])
            self.signals_layout.insertWidget(self.signals_layout.count() - 1, w)

    def _fill_lane_combo(self, sid: str, current: str) -> None:
        combo = self.rows[sid].lane_combo
        assert combo is not None
        combo.blockSignals(True)
        combo.clear()
        for key, label in self._lanes:
            combo.addItem(label, key)
        combo.addItem("New lane", NEW_LANE)
        combo.setCurrentIndex(max(combo.findData(current), 0))
        combo.blockSignals(False)
        try:
            combo.currentIndexChanged.disconnect()
        except TypeError:
            pass  # nothing connected yet
        combo.currentIndexChanged.connect(lambda _i, s=sid: self._on_lane_chosen(s))

    def lane_of(self, sid: str) -> str | None:
        combo = self.rows[sid].lane_combo if sid in self.rows else None
        data = combo.currentData() if combo is not None else None
        return data if isinstance(data, str) and data != NEW_LANE else None

    def _on_lane_chosen(self, sid: str) -> None:
        combo = self.rows[sid].lane_combo
        assert combo is not None
        key = combo.currentData()
        if key == NEW_LANE:
            n = len(self._lanes) + 1
            taken = {k for k, _ in self._lanes}
            while f"Lane {n}" in taken:
                n += 1
            key = f"Lane {n}"
            self._lanes.append((key, key))
            currents = {s: (self.lane_of(s) or "") for s in self.rows if s != sid}
            for other, lane in currents.items():
                self._fill_lane_combo(other, lane)
            self._fill_lane_combo(sid, key)
        label = next((lbl for k, lbl in self._lanes if k == key), str(key))
        self.signal_lane_changed.emit(sid, str(key), label)
