"""
The log of commands sent to the device (R6.3, R9.5), under the control panel.

Each send gets a number, also shown on the plot as a dashed marker at the stream time it
was sent, so a change in the response can be matched to the command that caused it. A
line says what changed since the previous send of those values (`kp 0.1 → 0.25`). A
refused send (a value that doesn't fit, not connected) is logged too, in red, without a
number. Plain grey lines under a hairline, no table: double-click a line to send its exact
packet again; the bytes are in its tooltip.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6 import QtCore, QtGui, QtWidgets

from styles import BORDER, TEXT_DIM, mono_font

ERROR_COLOR = "#FF4040"
MAX_ROWS = 500


@dataclass(frozen=True)
class LogEntry:
    number: int | None  # None: not sent
    when: str  # wall clock, HH:MM:SS
    label: str  # the button pressed
    detail: str  # what changed, or why it wasn't sent
    packet: bytes = b""
    command: str = ""

    @property
    def sent(self) -> bool:
        return self.number is not None


class CommandLog(QtWidgets.QWidget):
    resend_requested = QtCore.pyqtSignal(object)  # LogEntry

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 6)
        layout.setSpacing(0)
        self.list = QtWidgets.QListWidget()
        self.list.setObjectName("send_log")
        self.list.setFont(mono_font(max(QtGui.QGuiApplication.font().pointSizeF() - 2, 8)))
        self.list.setStyleSheet(
            f"QListWidget#send_log {{ background: transparent; border: none;"
            f" border-top: 1px solid {BORDER}; color: {TEXT_DIM}; }}"
            " QListWidget#send_log::item { padding: 1px 0; }"
        )
        self.list.setWordWrap(True)  # the whole change is worth reading
        self.list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.list, 1)
        self._entries: list[LogEntry] = []

    def add(self, entry: LogEntry) -> None:
        """Adds a line at the top (newest first)."""
        self._entries.insert(0, entry)
        number = f"▲{entry.number}" if entry.number is not None else "✕"
        text = f"{entry.when}  {number}  {entry.label}"
        if entry.detail:
            text += f"  {entry.detail}"
        item = QtWidgets.QListWidgetItem(text)
        if entry.sent:
            item.setToolTip(
                f"{entry.detail}\n{entry.packet.hex(' ').upper()}\nDouble-click to send it again"
            )
        else:
            item.setToolTip(entry.detail)
            item.setForeground(QtGui.QColor(ERROR_COLOR))
        self.list.insertItem(0, item)
        while self.list.count() > MAX_ROWS:
            self.list.takeItem(self.list.count() - 1)
            self._entries.pop()

    def entries(self) -> list[LogEntry]:
        """Newest first."""
        return list(self._entries)

    def line(self, index: int) -> str:
        """The shown text of a line (0 = newest)."""
        item = self.list.item(index)
        return item.text() if item is not None else ""

    def _on_double_click(self, item: QtWidgets.QListWidgetItem) -> None:
        index = self.list.row(item)
        entry = self._entries[index] if 0 <= index < len(self._entries) else None
        if entry is not None and entry.sent:
            self.resend_requested.emit(entry)
