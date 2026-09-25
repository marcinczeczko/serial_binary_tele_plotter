"""
The streams as tabs above the plot (R6.1).

Every configured stream is decoded all the time, so a tab is only a view choice. Each tab
shows whether its stream is receiving data and at what rate, so a stream whose frames never
arrive (a wrong `stream_id`, a layout mismatch) is visible without opening it.

The class keeps the few `QComboBox` calls the sidebar used (`addItem`, `findData`,
`currentData`, `itemData`) so the stream-switching logic didn't have to change.
"""

from __future__ import annotations

from typing import Any

from PyQt6 import QtGui, QtWidgets

ACTIVE_COLOR = "#E0E0E0"
IDLE_COLOR = "#8A8A8A"


class StreamTabs(QtWidgets.QTabBar):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDrawBase(False)
        self.setExpanding(False)
        self.setDocumentMode(True)
        self.setUsesScrollButtons(True)
        self._names: list[str] = []

    # --- QComboBox-like API ---

    def addItem(self, name: str, key: str) -> None:  # noqa: N802 (mirrors QComboBox)
        index = self.addTab(name)
        self.setTabData(index, key)
        self._names.append(name)
        self.setTabToolTip(index, f"{name}: waiting for data")

    def clear(self) -> None:
        while self.count():
            self.removeTab(self.count() - 1)
        self._names.clear()

    def findData(self, key: Any) -> int:  # noqa: N802
        return next((i for i in range(self.count()) if self.tabData(i) == key), -1)

    def itemData(self, index: int) -> Any:  # noqa: N802
        return self.tabData(index) if 0 <= index < self.count() else None

    def currentData(self) -> Any:  # noqa: N802
        return self.itemData(self.currentIndex())

    # --- activity ---

    def set_activity(self, key: str, rate_hz: float | None) -> None:
        """Shows a stream's sample rate on its tab (None or 0: nothing received lately)."""
        index = self.findData(key)
        if index < 0:
            return
        name = self._names[index]
        receiving = rate_hz is not None and rate_hz > 0
        suffix = f"{rate_hz:.0f} Hz" if receiving else "no data"
        self.setTabText(index, f"{name} · {suffix}")
        self.setTabTextColor(index, QtGui.QColor(ACTIVE_COLOR if receiving else IDLE_COLOR))
        self.setTabToolTip(
            index,
            f"{name}: {suffix}" + ("" if receiving else " (no frames of this stream arrived)"),
        )
