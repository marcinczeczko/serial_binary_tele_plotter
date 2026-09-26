"""
The frame as one strip (R11.2): what the device sends, in wire order, each field as wide as
its bytes.

The bus-decode grid (`FrameView`) was the loudest thing in the editor and repeated the
signal list; the strip keeps the one thing only it shows, where a field sits in the frame.
Cells are grey. A plotted field has its signal's colour as a top edge (grey when hidden at
open), the selected field is filled. A name is drawn only where it fits; the tooltip has
name, type and byte. Under the strip: byte offsets, the selected field and the frame's size.
Click selects, right-click asks for the field menu, as in `FrameView`.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6 import QtCore, QtGui, QtWidgets

from core.config import MAX_PAYLOAD_BYTES
from core.config.draft import FieldSlot
from styles import BORDER, RED, TEXT, TEXT_DISABLED, TEXT_MUTED, mono_font, number_font

PAD_X = 12
PAD_TOP = 10
CELL_HEIGHT = 24
EDGE = 3  # the plotted colour's top edge
LABEL_GAP = 3
LABEL_HEIGHT = 16
PAD_BOTTOM = 8
CELL_FILL = QtGui.QColor("#111111")
UNPLOTTED_FILL = QtGui.QColor("#000000")
CELL_BORDER = QtGui.QColor("#3a3a3a")
HIDDEN_EDGE = QtGui.QColor("#4a4a4a")
NAME_TEXT = QtGui.QColor("#8a8a8a")


@dataclass(frozen=True)
class Piece:
    """A field's cell."""

    slot: FieldSlot
    rect: QtCore.QRectF


class FrameStrip(QtWidgets.QWidget):
    field_clicked = QtCore.pyqtSignal(int)  # field index
    menu_requested = QtCore.pyqtSignal(int, QtCore.QPoint)  # field index, global position

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._slots: list[FieldSlot] = []
        self._colors: dict[str, str] = {}
        self._hidden: frozenset[str] = frozenset()
        self._selected: int | None = None
        self.setMouseTracking(True)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed
        )
        self.setFixedHeight(PAD_TOP + CELL_HEIGHT + LABEL_GAP + LABEL_HEIGHT + PAD_BOTTOM)

    # --- content ---

    def set_frame(
        self,
        slots: list[FieldSlot],
        colors: dict[str, str],
        selected: int | None = None,
        hidden: frozenset[str] = frozenset(),
    ) -> None:
        """`colors`: plotted field -> its signal's colour; `hidden`: plotted but not shown."""
        self._slots = slots
        self._colors = colors
        self._hidden = hidden
        self._selected = selected
        self.update()

    def set_selected(self, index: int | None) -> None:
        self._selected = index
        self.update()

    @property
    def selected(self) -> int | None:
        return self._selected

    def total(self) -> int:
        return sum(s.size for s in self._slots)

    def size_text(self) -> str:
        return f"{self.total()} / {MAX_PAYLOAD_BYTES} B"

    # --- geometry ---

    def _x(self, byte: int) -> float:
        width = self.width() - 2 * PAD_X
        return PAD_X + width * byte / max(self.total(), 1)

    def pieces(self) -> list[Piece]:
        return [
            Piece(
                slot,
                QtCore.QRectF(
                    self._x(slot.offset),
                    PAD_TOP,
                    self._x(slot.offset + slot.size) - self._x(slot.offset),
                    CELL_HEIGHT,
                ),
            )
            for slot in self._slots
        ]

    def field_at(self, pos: QtCore.QPointF) -> int | None:
        for piece in self.pieces():
            if piece.rect.contains(pos):
                return piece.slot.index
        return None

    # --- painting ---

    def paintEvent(self, event: QtGui.QPaintEvent | None) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        name_font = mono_font()
        name_font.setPixelSize(11)
        metrics = QtGui.QFontMetrics(name_font)
        for piece in self.pieces():
            self._draw_cell(painter, piece, name_font, metrics)
        self._draw_labels(painter)
        painter.end()

    def _draw_cell(
        self,
        painter: QtGui.QPainter,
        piece: Piece,
        font: QtGui.QFont,
        metrics: QtGui.QFontMetrics,
    ) -> None:
        r = piece.rect
        name = piece.slot.name
        color = self._colors.get(name)
        selected = piece.slot.index == self._selected
        if selected:
            painter.fillRect(r, QtGui.QColor(color or TEXT))
        else:
            painter.fillRect(r, CELL_FILL if color else UNPLOTTED_FILL)
            if color:
                edge = HIDDEN_EDGE if name in self._hidden else QtGui.QColor(color)
                painter.fillRect(QtCore.QRectF(r.left(), r.top(), r.width(), EDGE), edge)
        painter.setPen(CELL_BORDER)
        painter.drawRect(r.adjusted(0, 0, 0, -0.5))
        text_rect = r.adjusted(4, EDGE, -4, 0)
        if metrics.horizontalAdvance(name) <= text_rect.width():
            bold = QtGui.QFont(font)
            bold.setBold(selected)
            painter.setFont(bold)
            painter.setPen(QtGui.QColor("#000") if selected else NAME_TEXT)
            painter.drawText(text_rect, int(QtCore.Qt.AlignmentFlag.AlignCenter), name)

    def _draw_labels(self, painter: QtGui.QPainter) -> None:
        font = number_font()
        font.setPixelSize(11)
        painter.setFont(font)
        metrics = QtGui.QFontMetrics(font)
        top = PAD_TOP + CELL_HEIGHT + LABEL_GAP
        total = self.total()
        step = 16 if total > 64 else 4
        painter.setPen(QtGui.QColor(TEXT_DISABLED))
        for byte in range(step, total - step // 2, step):
            x = self._x(byte)
            painter.fillRect(QtCore.QRectF(x, top, 1, LABEL_HEIGHT - 2), QtGui.QColor(BORDER))
            painter.drawText(QtCore.QPointF(x + 3, top + metrics.ascent()), f"+{byte}")

        size = self.size_text()
        size_w = metrics.horizontalAdvance(size)
        size_x = self.width() - PAD_X - size_w
        slot = next((s for s in self._slots if s.index == self._selected), None)
        if slot is not None:
            tag = f"{slot.name}  {slot.type}  +{slot.offset}"
            tag_w = metrics.horizontalAdvance(tag) + 6
            x = min(self._x(slot.offset), size_x - tag_w - 12)
            painter.fillRect(QtCore.QRectF(x, top, tag_w, LABEL_HEIGHT), QtGui.QColor("#000"))
            painter.setPen(QtGui.QColor(TEXT))
            painter.drawText(QtCore.QPointF(x + 3, top + metrics.ascent()), tag)
        painter.fillRect(
            QtCore.QRectF(size_x - 8, top, size_w + 8, LABEL_HEIGHT), QtGui.QColor("#000")
        )
        painter.setPen(QtGui.QColor(RED if total > MAX_PAYLOAD_BYTES else TEXT_MUTED))
        painter.drawText(QtCore.QPointF(size_x, top + metrics.ascent()), size)

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
