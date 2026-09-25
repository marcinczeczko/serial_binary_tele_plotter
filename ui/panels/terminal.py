"""
The terminal a text profile sends from (R8.5, ADR-0011), in the Controls dock.

Type a line and press Enter: it's sent with the chosen line ending, numbered like a panel
send (`▲ 3`, also a marker on the plot) and shown in the transcript. Up/Down walk the
lines sent this session, Esc clears the input. The board's replies (lines no stream
pattern matches) arrive about once a second from the link report and are shown in grey
under what was sent. Telemetry lines never appear here.
"""

from __future__ import annotations

import html

from PyQt6 import QtCore, QtGui, QtWidgets

from styles import MONO_FAMILY

ENDINGS: dict[str, bytes] = {"LF": b"\n", "CR LF": b"\r\n", "CR": b"\r", "none": b""}
DEFAULT_ENDING = "LF"
MAX_ROWS = 500
HISTORY = 50
MONO = f"'{MONO_FAMILY}', Menlo, 'DejaVu Sans Mono', monospace"
SENT = "#ffffff"
NUMBER = "#FFB000"
REPLY = "#888888"
TIME = "#666666"
REFUSED = "#FF4040"


class TerminalInput(QtWidgets.QLineEdit):
    """The input line: Up/Down walk the history, Esc clears."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.history: list[str] = []
        self._at = 0  # position in the history; len(history) means the line being typed
        self._draft = ""

    def remember(self, line: str) -> None:
        if not self.history or self.history[-1] != line:
            self.history.append(line)
            del self.history[:-HISTORY]
        self._at = len(self.history)
        self._draft = ""

    def keyPressEvent(self, event: QtGui.QKeyEvent | None) -> None:  # noqa: N802
        key = event.key() if event is not None else None
        if key == QtCore.Qt.Key.Key_Up and self.history:
            if self._at == len(self.history):
                self._draft = self.text()
            self._at = max(0, self._at - 1)
            self.setText(self.history[self._at])
        elif key == QtCore.Qt.Key.Key_Down and self._at < len(self.history):
            self._at += 1
            self.setText(self.history[self._at] if self._at < len(self.history) else self._draft)
        elif key == QtCore.Qt.Key.Key_Escape:
            self.clear()
            self._at = len(self.history)
        else:
            super().keyPressEvent(event)


class Terminal(QtWidgets.QWidget):
    line_entered = QtCore.pyqtSignal(str)  # the typed line, without its ending
    ending_changed = QtCore.pyqtSignal(str)  # an ENDINGS key

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        self.transcript = QtWidgets.QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setMaximumBlockCount(MAX_ROWS)
        self.transcript.setStyleSheet(f"QPlainTextEdit {{ font-family: {MONO}; border: none; }}")
        layout.addWidget(self.transcript, 1)

        row = QtWidgets.QHBoxLayout()
        prompt = QtWidgets.QLabel(">")
        prompt.setStyleSheet(f"color: #888; font-family: {MONO};")
        self.input = TerminalInput()
        self.input.setStyleSheet(f"QLineEdit {{ font-family: {MONO}; }}")
        self.input.setToolTip("Enter sends the line. Up/Down: lines sent before. Esc clears.")
        self.ending_combo = QtWidgets.QComboBox()
        self.ending_combo.addItems(list(ENDINGS))
        self.ending_combo.setToolTip("Line ending added to each line sent")
        row.addWidget(prompt)
        row.addWidget(self.input, 1)
        row.addWidget(self.ending_combo)
        layout.addLayout(row)

        self.input.returnPressed.connect(self._on_enter)
        self.ending_combo.activated.connect(
            lambda _i: self.ending_changed.emit(self.ending_combo.currentText())
        )
        self.set_connected(False)

    # --- state ---

    @property
    def ending(self) -> bytes:
        return ENDINGS.get(self.ending_combo.currentText(), ENDINGS[DEFAULT_ENDING])

    def set_ending(self, name: str) -> None:
        self.ending_combo.setCurrentText(name if name in ENDINGS else DEFAULT_ENDING)

    def set_connected(self, connected: bool) -> None:
        self.input.setEnabled(connected)
        self.input.setPlaceholderText("" if connected else "Connect to send")

    def _on_enter(self) -> None:
        line = self.input.text()
        if not line.strip():
            return
        self.input.remember(line)
        self.input.clear()
        self.line_entered.emit(line)

    # --- transcript ---

    def _row(self, number: str, when: str, text: str, color: str) -> None:
        padded = html.escape(number.ljust(5)).replace(" ", "&nbsp;")
        self.transcript.appendHtml(
            f'<span style="color:{NUMBER}">{padded}</span>'
            f'<span style="color:{TIME}">&nbsp;{html.escape(when)}&nbsp;</span>'
            f'<span style="color:{color}">{html.escape(text)}</span>'
        )

    def add_sent(self, number: int, when: str, line: str) -> None:
        self._row(f"▲ {number}", when, line, SENT)

    def add_refused(self, when: str, line: str, reason: str) -> None:
        self._row("✕", when, f"{line} ({reason})", REFUSED)

    def add_replies(self, when: str, lines: list[str], dropped: int = 0) -> None:
        if dropped:
            self._row("", when, f"… {dropped} more lines not shown", REPLY)
        for line in lines:
            self._row("", when, line, REPLY)

    def rows(self) -> list[str]:
        """The transcript as plain text, one row per line (for tests)."""
        return self.transcript.toPlainText().splitlines()
