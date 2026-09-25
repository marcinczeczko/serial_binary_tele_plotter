"""
"From console output…" (R8.4): paste what the board prints, or listen for a few
seconds, and get one stream per repeated line layout.

Left, the paste box and **Listen on the port for 5 s** (only while connected; the engine
hands the lines over once, `TelemetryEngine.listen_lines`). Right, a card per pattern
found (`core.config.infer_lines`): tick it, name it, check its values. Create adds the
ticked ones to the profile as new streams.
"""

from __future__ import annotations

from typing import Any

from PyQt6 import QtCore, QtWidgets

from core.config.infer_lines import InferredPattern, infer_patterns
from core.protocol.text_line import parse_pattern
from ui.config.frame_view import TIME_FIELD
from ui.config.line_view import LineView
from ui.config.stream_editor import MONO_STYLE

LISTEN_S = 5
INFER_DELAY_MS = 200  # re-read the box once typing pauses


def _colors(stream: dict[str, Any]) -> dict[str, str]:
    colors = {str(s["field"]): str(s["color"]) for s in stream.get("signals", {}).values()}
    axis = stream.get("time", {}).get("field")
    if isinstance(axis, str):
        colors[axis] = TIME_FIELD
    return colors


class PatternCard(QtWidgets.QFrame):
    """One line pattern: create it or not, its stream name, its values."""

    toggled = QtCore.pyqtSignal()

    def __init__(
        self, found: InferredPattern, checked: bool, name: str, parent: QtWidgets.QWidget
    ) -> None:
        super().__init__(parent)
        self.found = found
        self.setObjectName("card")
        self.setStyleSheet("QFrame#card { border: 1px solid #333; }")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(0)

        head_box = QtWidgets.QWidget()
        head_box.setStyleSheet("background: #111;")
        head = QtWidgets.QHBoxLayout(head_box)
        head.setContentsMargins(10, 5, 10, 5)
        self.check = QtWidgets.QCheckBox()
        self.check.setChecked(checked)
        self.check.setToolTip("Create this stream")
        self.name_edit = QtWidgets.QLineEdit(name)
        self.name_edit.setFixedWidth(150)
        self.name_edit.setToolTip("The stream's name")
        pattern = QtWidgets.QLabel(found.pattern)
        pattern.setStyleSheet("color: #bbb; " + MONO_STYLE)
        pattern.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        count = QtWidgets.QLabel(f"{len(found.lines)} lines")
        count.setStyleSheet("color: #888;")
        head.addWidget(self.check)
        head.addWidget(self.name_edit)
        head.addWidget(pattern)
        head.addStretch()
        head.addWidget(count)
        layout.addWidget(head_box)

        stream = found.stream()
        self.line_view = LineView(show_line=False)
        self.line_view.set_pattern(parse_pattern(found.pattern), _colors(stream))
        layout.addWidget(self.line_view)

        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(10, 4, 10, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)
        for row, value in enumerate(found.values):
            cells = (value.name, value.type, value.range_text, value.note)
            styles = ("color: white;", "color: #888;", "color: #888;", "color: #FFB000;")
            for col, (text, style) in enumerate(zip(cells, styles, strict=True)):
                lbl = QtWidgets.QLabel(text)
                lbl.setStyleSheet(style + (MONO_STYLE if col < 3 else ""))
                grid.addWidget(lbl, row, col)
        grid.setColumnStretch(3, 1)
        layout.addLayout(grid)

        self.check.toggled.connect(self._on_toggled)
        self._on_toggled(checked, emit=False)

    def _on_toggled(self, on: bool, emit: bool = True) -> None:
        effect = QtWidgets.QGraphicsOpacityEffect(self)
        effect.setOpacity(1.0 if on else 0.5)
        self.setGraphicsEffect(effect)
        if emit:
            self.toggled.emit()

    @property
    def checked(self) -> bool:
        return self.check.isChecked()

    @property
    def name(self) -> str:
        return self.name_edit.text().strip() or self.found.name


class ConsoleOutputDialog(QtWidgets.QDialog):
    listen_requested = QtCore.pyqtSignal(float)  # seconds
    stop_requested = QtCore.pyqtSignal()

    def __init__(
        self, profile_name: str, connected: bool, parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Streams from console output")
        self.resize(1100, 680)
        self.cards: list[PatternCard] = []
        self._listening = False
        self._left = 0
        self._lines = 0
        self._build(profile_name, connected)

    def _build(self, profile_name: str, connected: bool) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(12)

        left = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel("Paste what the console shows")
        title.setStyleSheet("color: #aaa; font-weight: bold;")
        self.paste_box = QtWidgets.QPlainTextEdit()
        self.paste_box.setStyleSheet("QPlainTextEdit { " + MONO_STYLE + " }")
        self.paste_box.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.listen_btn = QtWidgets.QPushButton(f"Listen on the port for {LISTEN_S} s")
        self.listen_btn.setStyleSheet(
            "QPushButton:disabled { color: #666; background: #1a1a1a; border: 1px solid #333; }"
        )
        self.listen_btn.setEnabled(connected)
        self.listen_btn.setToolTip("" if connected else "Connect first")
        left.addWidget(title)
        left.addWidget(self.paste_box, 1)
        left.addWidget(self.listen_btn, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        left_box = QtWidgets.QWidget()
        left_box.setLayout(left)
        left_box.setFixedWidth(400)
        body.addWidget(left_box)

        right = QtWidgets.QVBoxLayout()
        head = QtWidgets.QHBoxLayout()
        found = QtWidgets.QLabel("Line patterns found")
        found.setStyleSheet("color: #aaa; font-weight: bold;")
        into = QtWidgets.QLabel(f"into {profile_name}")
        into.setStyleSheet("color: #888;")
        head.addWidget(found)
        head.addStretch()
        head.addWidget(into)
        right.addLayout(head)
        self.cards_box = QtWidgets.QVBoxLayout()
        self.cards_box.setSpacing(12)
        holder = QtWidgets.QWidget()
        holder_layout = QtWidgets.QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.addLayout(self.cards_box)
        self.seen_once_lbl = QtWidgets.QLabel("")
        self.seen_once_lbl.setStyleSheet("color: #888;")
        self.seen_once_lbl.setWordWrap(True)
        holder_layout.addWidget(self.seen_once_lbl)
        holder_layout.addStretch()
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setWidget(holder)
        right.addWidget(scroll, 1)
        body.addLayout(right, 1)
        outer.addLayout(body, 1)

        footer = QtWidgets.QHBoxLayout()
        self.summary_lbl = QtWidgets.QLabel("")
        self.summary_lbl.setStyleSheet("color: #888;")
        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.create_btn = QtWidgets.QPushButton("Create streams")
        self.create_btn.setDefault(True)
        footer.addWidget(self.summary_lbl)
        footer.addStretch()
        footer.addWidget(self.cancel_btn)
        footer.addWidget(self.create_btn)
        outer.addLayout(footer)

        self._infer_timer = QtCore.QTimer(self)
        self._infer_timer.setSingleShot(True)
        self._infer_timer.setInterval(INFER_DELAY_MS)
        self._infer_timer.timeout.connect(self.infer)
        self._countdown = QtCore.QTimer(self)
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self._tick)
        self.paste_box.textChanged.connect(self._infer_timer.start)
        self.listen_btn.clicked.connect(self.listen)
        self.cancel_btn.clicked.connect(self.reject)
        self.create_btn.clicked.connect(self.accept)
        self.infer()

    # --- inference ---

    def infer(self) -> None:
        """Re-reads the box; a pattern keeps its tick and name across re-reads."""
        self._infer_timer.stop()
        kept = {c.found.pattern: (c.checked, c.name_edit.text()) for c in self.cards}
        for card in self.cards:
            self.cards_box.removeWidget(card)
            card.deleteLater()
        result = infer_patterns(self.paste_box.toPlainText().splitlines())
        self.cards = []
        for found in result.patterns:
            checked, name = kept.get(found.pattern, (True, found.name))
            card = PatternCard(found, checked, name, self)
            card.toggled.connect(self._update_footer)
            self.cards_box.addWidget(card)
            self.cards.append(card)
        once = result.seen_once
        self.seen_once_lbl.setText(
            f"Not a pattern (seen once): {'   '.join(once[:5])}{' …' if len(once) > 5 else ''}"
            if once
            else ""
        )
        self._lines = result.line_count
        self._update_footer()

    def _update_footer(self) -> None:
        chosen = [c for c in self.cards if c.checked]
        values = sum(len(c.found.values) for c in chosen)
        self.summary_lbl.setText(
            f"{len(chosen)} of {len(self.cards)} streams, {values} values from {self._lines} lines"
            if self.cards
            else ("No line layout seen twice yet" if self._lines else "")
        )
        self.create_btn.setText(f"Create {len(chosen)} stream{'s' if len(chosen) != 1 else ''}")
        self.create_btn.setEnabled(bool(chosen) and not self._listening)

    def results(self) -> list[tuple[str, dict[str, Any], str]]:
        """(name, stream, a line it matches) of each ticked pattern, in order."""
        out = []
        for card in self.cards:
            if card.checked:
                stream = card.found.stream()
                stream["name"] = card.name
                out.append((card.name, stream, card.found.lines[-1]))
        return out

    # --- listening ---

    def listen(self) -> None:
        self._listening = True
        self._left = LISTEN_S
        self.listen_btn.setEnabled(False)
        self._tick(start=True)
        self._countdown.start()
        self._update_footer()
        self.listen_requested.emit(float(LISTEN_S))

    def _tick(self, start: bool = False) -> None:
        if not start:
            self._left = max(0, self._left - 1)
        self.listen_btn.setText(f"Listening… {self._left} s")

    def on_lines_heard(self, lines: list[str]) -> None:
        """The engine's lines: added to the box, which is read again."""
        if not self._listening:
            return
        self._listening = False
        self._countdown.stop()
        self.listen_btn.setText(f"Listen on the port for {LISTEN_S} s")
        self.listen_btn.setEnabled(True)
        if lines:
            text = self.paste_box.toPlainText()
            joined = "\n".join(lines)
            self.paste_box.setPlainText(f"{text.rstrip()}\n{joined}" if text.strip() else joined)
        self.infer()

    def set_connected(self, connected: bool) -> None:
        if not self._listening:
            self.listen_btn.setEnabled(connected)
        self.listen_btn.setToolTip("" if connected else "Connect first")

    def reject(self) -> None:
        if self._listening:
            self.stop_requested.emit()
            self._listening = False
        super().reject()
