"""
The streams as tabs in the top bar (R6.1, R9.2).

Every configured stream is decoded all the time, so a tab is only a view choice. A 6 px
square before each name is green while the stream's frames arrive and hollow when they
don't, so a stream whose frames never arrive (a wrong `stream_id`, a layout mismatch) is
visible without opening it. The rate is in the tooltip.

The class keeps the few `QComboBox` calls the sidebar used (`addItem`, `findData`,
`currentData`, `itemData`) so the stream-switching logic didn't have to change.
"""

from __future__ import annotations

from typing import Any

from PyQt6 import QtCore, QtGui, QtWidgets

from styles import GREEN, TEXT_MUTED

SQUARE_PX = 6


def _square(receiving: bool) -> QtGui.QIcon:
    size = SQUARE_PX * 2  # drawn at 2x for sharp edges on a high-density screen
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    if receiving:
        painter.fillRect(0, 0, size, size, QtGui.QColor(GREEN))
    else:
        painter.setPen(QtGui.QPen(QtGui.QColor(TEXT_MUTED), 2))
        painter.drawRect(1, 1, size - 2, size - 2)
    painter.end()
    pixmap.setDevicePixelRatio(2.0)
    return QtGui.QIcon(pixmap)


class StreamTabs(QtWidgets.QTabBar):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDrawBase(False)
        self.setExpanding(False)
        self.setDocumentMode(True)
        self.setUsesScrollButtons(True)
        self.setObjectName("stream_tabs")
        self.setIconSize(QtCore.QSize(SQUARE_PX, SQUARE_PX))
        self.setStyleSheet(
            "QTabBar#stream_tabs::tab { padding: 0 14px; height: 33px; font-size: 13px; }"
        )
        self._names: list[str] = []
        self._rates: dict[str, float | None] = {}

    # --- QComboBox-like API ---

    def addItem(self, name: str, key: str) -> None:  # noqa: N802 (mirrors QComboBox)
        index = self.addTab(name)
        self.setTabData(index, key)
        self._names.append(name)
        self.setTabIcon(index, _square(False))
        self.setTabToolTip(index, f"{name}: waiting for data")

    def clear(self) -> None:
        while self.count():
            self.removeTab(self.count() - 1)
        self._names.clear()
        self._rates.clear()

    def findData(self, key: Any) -> int:  # noqa: N802
        return next((i for i in range(self.count()) if self.tabData(i) == key), -1)

    def itemData(self, index: int) -> Any:  # noqa: N802
        return self.tabData(index) if 0 <= index < self.count() else None

    def currentData(self) -> Any:  # noqa: N802
        return self.itemData(self.currentIndex())

    # --- activity ---

    def set_activity(self, key: str, rate_hz: float | None) -> None:
        """A stream's sample rate (None or 0: nothing received lately): square and tooltip."""
        index = self.findData(key)
        if index < 0:
            return
        name = self._names[index]
        receiving = rate_hz is not None and rate_hz > 0
        self._rates[key] = rate_hz if receiving else None
        self.setTabIcon(index, _square(receiving))
        self.setTabToolTip(
            index,
            f"{name}: {rate_hz:.0f} Hz"
            if receiving
            else f"{name}: no data (no frames of this stream arrived)",
        )

    def rate(self, key: str) -> float | None:
        """The stream's last reported rate, None while nothing arrives."""
        return self._rates.get(key)
