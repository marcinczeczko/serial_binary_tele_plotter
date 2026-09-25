"""
A text stream's line, drawn like a bus decode (R8.4): the text-profile counterpart of
`FrameView`.

The first row is the pattern: fixed text in grey monospace, each value a hexagon in the
color of the signal drawn from it (the selected one filled). The second row is the last
line seen for the stream and whether the pattern matches it. While the pattern being
typed is invalid the row stays on the last valid one, dimmed. Clicking a value selects it.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6 import QtCore, QtGui, QtWidgets

from core.protocol.text_line import Pattern, Slot
from ui.config.frame_view import BORDER, SCOPE_BG, UNPLOTTED, draw_block

PAD = 8
ROW_HEIGHT = 22
ROW_GAP = 7
LINE_ROW = 14
GAP = 3  # between neighbouring pieces
MIN_BLOCK = 44
TEXT = QtGui.QColor("#999999")
LABEL = QtGui.QColor("#777777")
LINE = QtGui.QColor("#bbbbbb")
EMPTY = QtGui.QColor("#888888")
MATCH = QtGui.QColor("#4cc38a")
NO_MATCH = QtGui.QColor("#888888")
NO_LINE = "none yet: connect, or paste console output"


@dataclass(frozen=True)
class Piece:
    """Fixed text (`index` None) or value `index` of the pattern, where it's drawn."""

    text: str
    index: int | None
    rect: QtCore.QRectF


class LineView(QtWidgets.QWidget):
    field_clicked = QtCore.pyqtSignal(int)  # value index
    menu_requested = QtCore.pyqtSignal(int, QtCore.QPoint)  # value index, global position

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._pattern: Pattern | None = None
        self._colors: dict[str, str] = {}
        self._selected: int | None = None
        self._dimmed = False
        self._line: str | None = None
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed
        )
        self.setFixedHeight(PAD + ROW_HEIGHT + ROW_GAP + LINE_ROW + PAD + 1)

    # --- content ---

    def set_pattern(
        self,
        pattern: Pattern | None,
        colors: dict[str, str],
        selected: int | None = None,
        dimmed: bool = False,
    ) -> None:
        self._pattern = pattern
        self._colors = colors
        self._selected = selected
        self._dimmed = dimmed
        self.update()

    def set_selected(self, index: int | None) -> None:
        self._selected = index
        self.update()

    def set_line(self, line: str | None) -> None:
        """The last line seen for the stream; None when there is none yet."""
        self._line = line
        self.update()

    @property
    def line(self) -> str | None:
        return self._line

    def matches(self) -> bool | None:
        """Whether the pattern matches the last line; None without either."""
        if self._pattern is None or self._line is None:
            return None
        return self._pattern.regex.fullmatch(self._line.strip()) is not None

    # --- geometry ---

    def _mono(self) -> QtGui.QFont:
        mono = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        mono.setPointSizeF(max(7.0, self.font().pointSizeF() - 1.5))
        return mono

    def pieces(self) -> list[Piece]:
        """Fixed text at its own width; the values share what's left equally."""
        if self._pattern is None:
            return []
        metrics = QtGui.QFontMetricsF(self._mono())
        tokens = self._pattern.tokens
        texts = [metrics.horizontalAdvance(t) for t in tokens if not isinstance(t, Slot)]
        n_values = len(tokens) - len(texts)
        free = self.width() - 2 * PAD - sum(texts) - GAP * (len(tokens) - 1)
        block = max(MIN_BLOCK, free / max(1, n_values))
        out: list[Piece] = []
        x, index = float(PAD), 0
        for token in tokens:
            if isinstance(token, Slot):
                out.append(Piece(token.name, index, QtCore.QRectF(x, PAD, block, ROW_HEIGHT)))
                x += block
                index += 1
            else:
                w = metrics.horizontalAdvance(token)
                out.append(Piece(token, None, QtCore.QRectF(x, PAD, w, ROW_HEIGHT)))
                x += w
            x += GAP
        return out

    def field_at(self, pos: QtCore.QPointF) -> int | None:
        for piece in self.pieces():
            if piece.index is not None and piece.rect.contains(pos):
                return piece.index
        return None

    # --- painting ---

    def paintEvent(self, event: QtGui.QPaintEvent | None) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), SCOPE_BG)
        painter.setPen(BORDER)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        mono = self._mono()
        painter.setFont(mono)
        metrics = QtGui.QFontMetrics(mono)
        center = int(QtCore.Qt.AlignmentFlag.AlignVCenter)

        painter.setOpacity(0.45 if self._dimmed else 1.0)
        for piece in self.pieces():
            if piece.index is None:
                painter.setPen(TEXT)
                painter.drawText(piece.rect, center, piece.text)
            else:
                color = self._colors.get(piece.text, UNPLOTTED)
                selected = piece.index == self._selected
                draw_block(painter, piece.rect, piece.text, color, selected, metrics, mono)
        painter.setOpacity(1.0)

        y = PAD + ROW_HEIGHT + ROW_GAP
        x = float(PAD)
        painter.setPen(LABEL)
        label = "last line"
        painter.drawText(QtCore.QRectF(x, y, 200, LINE_ROW), center, label)
        x += metrics.horizontalAdvance(label) + 10
        verdict = {True: "✓ matches", False: "✗ no match", None: ""}[self.matches()]
        verdict_w = metrics.horizontalAdvance(verdict) + 10 if verdict else 0
        room = int(self.width() - PAD - x - verdict_w)
        if self._line is None:
            painter.setPen(EMPTY)
            painter.setFont(self.font())
            painter.drawText(QtCore.QRectF(x, y, room, LINE_ROW), center, NO_LINE)
            painter.end()
            return
        shown = metrics.elidedText(self._line, QtCore.Qt.TextElideMode.ElideRight, room)
        painter.setPen(LINE)
        painter.drawText(QtCore.QRectF(x, y, room, LINE_ROW), center, shown)
        x += metrics.horizontalAdvance(shown) + 10
        painter.setPen(MATCH if self.matches() else NO_MATCH)
        painter.drawText(QtCore.QRectF(x, y, verdict_w, LINE_ROW), center, verdict)
        painter.end()

    # --- input ---

    def mousePressEvent(self, event: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if event is None:
            return
        index = self.field_at(event.position())
        if index is None:
            return
        self.field_clicked.emit(index)
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            self.menu_requested.emit(index, event.globalPosition().toPoint())
