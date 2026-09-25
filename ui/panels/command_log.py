"""
The log of commands sent to the device (R6.3), under the control panels.

Each send gets a number, also shown on the plot as a dashed marker at the stream time it
was sent, so a change in the response can be matched to the command that caused it. A
row says what changed since the previous send of those values ("kp 0.10 → 0.25"). A
refused send (a value that doesn't fit, not connected) is logged too, in red, without a
number. "Send again" re-sends a row's exact packet.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6 import QtCore, QtGui, QtWidgets

ERROR_COLOR = "#FF9B9B"
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
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("<b>Sent</b>")
        header.addWidget(title)
        header.addStretch()
        self.resend_btn = QtWidgets.QPushButton("Send again")
        self.resend_btn.setEnabled(False)
        self.resend_btn.clicked.connect(self._resend_selected)
        header.addWidget(self.resend_btn)
        layout.addLayout(header)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["#", "Time", "Command", "Bytes"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.itemSelectionChanged.connect(self._on_selection)
        self.tree.itemDoubleClicked.connect(lambda *_: self._resend_selected())
        hdr = self.tree.header()
        assert hdr is not None
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for col in (0, 1, 3):
            hdr.setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.tree, 1)
        self._entries: list[LogEntry] = []

    def add(self, entry: LogEntry) -> None:
        """Adds a row at the top (newest first)."""
        self._entries.insert(0, entry)
        item = QtWidgets.QTreeWidgetItem(
            [
                f"▲ {entry.number}" if entry.number is not None else "✕",
                entry.when,
                f"{entry.label} · {entry.detail}" if entry.detail else entry.label,
                str(len(entry.packet)) if entry.sent else "",
            ]
        )
        item.setToolTip(2, entry.detail)
        if not entry.sent:
            for col in range(4):
                item.setForeground(col, QtGui.QColor(ERROR_COLOR))
        self.tree.insertTopLevelItem(0, item)
        while self.tree.topLevelItemCount() > MAX_ROWS:
            self.tree.takeTopLevelItem(self.tree.topLevelItemCount() - 1)
            self._entries.pop()

    def entries(self) -> list[LogEntry]:
        """Newest first."""
        return list(self._entries)

    def _selected(self) -> LogEntry | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        index = self.tree.indexOfTopLevelItem(item)
        return self._entries[index] if 0 <= index < len(self._entries) else None

    def _on_selection(self) -> None:
        entry = self._selected()
        self.resend_btn.setEnabled(entry is not None and entry.sent)

    def _resend_selected(self) -> None:
        entry = self._selected()
        if entry is not None and entry.sent:
            self.resend_requested.emit(entry)
