"""
The frame's byte layout, drawn like a bus decode on the scope (R7.1).

32 bytes per row, one hexagon per field in the color of the signal drawn from it: a field
that isn't plotted is grey, the X-axis counter is light grey, the selected field is
filled. Clicking a field selects it; right-clicking asks for its menu.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6 import QtCore, QtGui, QtWidgets

from core.config.draft import FieldSlot

BYTES_PER_ROW = 32
GUTTER = 44
ROW_HEIGHT = 22
ROW_GAP = 5
TICKS_HEIGHT = 14
PAD = 8
POINT = 5  # how far a hexagon's ends stick out
SCOPE_BG = QtGui.QColor("#1b1306")
BORDER = QtGui.QColor("#333333")
TICK_TEXT = QtGui.QColor("#777777")
OFFSET_TEXT = QtGui.QColor("#888888")
UNPLOTTED = "#777777"
TIME_FIELD = "#bbbbbb"


@dataclass(frozen=True)
class Piece:
    """A field's part on one row (a field crossing a row end has two)."""

    slot: FieldSlot
    rect: QtCore.QRectF


class FrameView(QtWidgets.QWidget):
    field_clicked = QtCore.pyqtSignal(int)  # field index
    menu_requested = QtCore.pyqtSignal(int, QtCore.QPoint)  # field index, global position

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._slots: list[FieldSlot] = []
        self._colors: dict[str, str] = {}
        self._selected: int | None = None
        self.setMouseTracking(True)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed
        )
        self._fit_height()

    # --- content ---

    def set_frame(
        self, slots: list[FieldSlot], colors: dict[str, str], selected: int | None = None
    ) -> None:
        """`colors`: field name -> the color it's drawn in (see `field_color`)."""
        self._slots = slots
        self._colors = colors
        self._selected = selected
        self._fit_height()
        self.update()

    def set_selected(self, index: int | None) -> None:
        self._selected = index
        self.update()

    @property
    def selected(self) -> int | None:
        return self._selected

    def rows(self) -> int:
        size = sum(s.size for s in self._slots)
        return max(1, -(-size // BYTES_PER_ROW))

    def _fit_height(self) -> None:
        self.setFixedHeight(PAD + TICKS_HEIGHT + self.rows() * (ROW_HEIGHT + ROW_GAP) + PAD)

    # --- geometry ---

    def byte_width(self) -> float:
        return (self.width() - 2 * PAD - GUTTER) / BYTES_PER_ROW

    def pieces(self) -> list[Piece]:
        out: list[Piece] = []
        bw = self.byte_width()
        for slot in self._slots:
            start, end = slot.offset, slot.offset + slot.size
            while start < end:
                row, col = divmod(start, BYTES_PER_ROW)
                n = min(end, (row + 1) * BYTES_PER_ROW) - start
                x = PAD + GUTTER + col * bw
                y = PAD + TICKS_HEIGHT + row * (ROW_HEIGHT + ROW_GAP)
                out.append(Piece(slot, QtCore.QRectF(x + 1, y, n * bw - 2, ROW_HEIGHT)))
                start += n
        return out

    def field_at(self, pos: QtCore.QPointF) -> int | None:
        for piece in self.pieces():
            if piece.rect.contains(pos):
                return piece.slot.index
        return None

    # --- painting ---

    def paintEvent(self, event: QtGui.QPaintEvent | None) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), SCOPE_BG)
        painter.setPen(BORDER)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        mono = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        mono.setPointSizeF(max(7.0, self.font().pointSizeF() - 1.5))
        painter.setFont(mono)
        bw = self.byte_width()
        painter.setPen(TICK_TEXT)
        for col in range(0, BYTES_PER_ROW, 4):
            x = PAD + GUTTER + col * bw
            painter.drawText(QtCore.QRectF(x, PAD - 2, 4 * bw, TICKS_HEIGHT), f"+{col}")
        painter.setPen(OFFSET_TEXT)
        for row in range(self.rows()):
            y = PAD + TICKS_HEIGHT + row * (ROW_HEIGHT + ROW_GAP)
            painter.drawText(
                QtCore.QRectF(PAD, y, GUTTER, ROW_HEIGHT),
                int(QtCore.Qt.AlignmentFlag.AlignVCenter),
                f"0x{row * BYTES_PER_ROW:02x}",
            )

        metrics = QtGui.QFontMetrics(mono)
        for piece in self.pieces():
            self._draw_piece(painter, piece, metrics, mono)
        painter.end()

    def _draw_piece(
        self,
        painter: QtGui.QPainter,
        piece: Piece,
        metrics: QtGui.QFontMetrics,
        font: QtGui.QFont,
    ) -> None:
        r = piece.rect
        point = min(POINT, r.width() / 3)
        path = QtGui.QPainterPath()
        path.moveTo(r.left() + point, r.top())
        path.lineTo(r.right() - point, r.top())
        path.lineTo(r.right(), r.center().y())
        path.lineTo(r.right() - point, r.bottom())
        path.lineTo(r.left() + point, r.bottom())
        path.lineTo(r.left(), r.center().y())
        path.closeSubpath()

        color = QtGui.QColor(self._colors.get(piece.slot.name, UNPLOTTED))
        selected = piece.slot.index == self._selected
        if selected:
            painter.setBrush(color)
            painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1))
        else:
            fill = QtGui.QColor(color)
            fill.setAlpha(38)
            painter.setBrush(fill)
            painter.setPen(QtGui.QPen(color, 1))
        painter.drawPath(path)

        text_rect = r.adjusted(point + 2, 0, -point - 2, 0)
        name = metrics.elidedText(
            piece.slot.name, QtCore.Qt.TextElideMode.ElideRight, int(text_rect.width())
        )
        bold = QtGui.QFont(font)
        bold.setBold(selected)
        painter.setFont(bold)
        painter.setPen(QtGui.QColor("#000000") if selected else color)
        painter.drawText(text_rect, int(QtCore.Qt.AlignmentFlag.AlignCenter), name)
        painter.setFont(font)

    # --- input ---

    def mousePressEvent(self, event: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if event is None:
            return
        index = self.field_at(event.position())
        if index is None:
            return
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            self.field_clicked.emit(index)
            self.menu_requested.emit(index, event.globalPosition().toPoint())
        elif event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.field_clicked.emit(index)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if event is None:
            return
        index = self.field_at(event.position())
        slot = next((s for s in self._slots if s.index == index), None)
        self.setToolTip(
            f"{slot.name} · {slot.type} · byte {slot.offset}" if slot is not None else ""
        )
