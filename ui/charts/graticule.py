"""
A lane's scope furniture (R9.6, ADR-0012): the graticule behind the traces and the markers
in front of them, each one item drawn in the ViewBox's pixel coordinates.

Render budget (R3.4): both items keep their geometry (the ViewBox's rect, changed only on
resize) and only paint. Nothing here moves or resizes an item from inside `paint()`, which
is what made a visible `TextItem` or a boxed `InfLineLabel` schedule a second paint per
frame. Their state changes call `update()`, which the next (single) paint picks up.

- `Graticule` (behind): a 1 px frame, 10 dotted vertical divisions, dotted horizontal lines
  at the Y axis ticks (the zero line brighter), and a centre crosshair with minor ticks,
  5 per division.
- `LaneMarkers` (in front): the trigger `T` on the right edge at the level and at the top at
  the trigger time, and the cursors' `A` (filled) / `B` (outlined) flags with the span
  between them faintly shaded.
"""

from __future__ import annotations

from collections.abc import Callable

import pyqtgraph as pg
from PyQt6 import QtCore, QtGui, QtWidgets

from styles import UI_FAMILY

FRAME = "#4a4a4a"
DIVISION = "#333333"
ZERO = "#5a5a5a"
CROSSHAIR = "#3a3a3a"
TRIGGER = "#FF9A1A"
CURSOR = "#C8C8C8"
DIVISIONS_X = 10
DIVISIONS_Y = 8  # for the crosshair's minor ticks only; horizontal lines follow the Y ticks
MINOR_PER_DIVISION = 5
MINOR_TICK_PX = 3
FLAG_PX = 14

TicksFn = Callable[[], list[float]]


def _dotted(color: str) -> QtGui.QPen:
    pen = QtGui.QPen(QtGui.QColor(color))
    pen.setCosmetic(True)
    pen.setWidth(1)
    pen.setStyle(QtCore.Qt.PenStyle.DotLine)
    return pen


def _solid(color: str) -> QtGui.QPen:
    pen = QtGui.QPen(QtGui.QColor(color))
    pen.setCosmetic(True)
    pen.setWidth(1)
    return pen


class _ViewItem(pg.GraphicsObject):  # type: ignore[misc]  # pyqtgraph is untyped
    """An item over the whole ViewBox, in its pixel coordinates; follows its size."""

    def __init__(self, vb: pg.ViewBox, z: float) -> None:
        super().__init__()
        self.vb = vb
        self._rect = QtCore.QRectF(vb.rect())
        self.setParentItem(vb)
        self.setZValue(z)
        self.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
        vb.sigResized.connect(self._on_resized)

    def _on_resized(self, *_: object) -> None:
        self.prepareGeometryChange()  # a resize, never during paint
        self._rect = QtCore.QRectF(self.vb.rect())
        self.update()

    def boundingRect(self) -> QtCore.QRectF:  # noqa: N802
        return self._rect

    def _y_px(self, y: float) -> float:
        return float(self.vb.mapFromView(QtCore.QPointF(0.0, y)).y())

    def _x_px(self, x: float) -> float:
        return float(self.vb.mapFromView(QtCore.QPointF(x, 0.0)).x())


class Graticule(_ViewItem):
    def __init__(self, vb: pg.ViewBox, y_ticks: TicksFn) -> None:
        super().__init__(vb, z=-1.0)  # above the background, below the traces
        self._y_ticks = y_ticks

    def paint(
        self,
        p: QtGui.QPainter | None,
        _option: QtWidgets.QStyleOptionGraphicsItem | None = None,
        _widget: QtWidgets.QWidget | None = None,
    ) -> None:
        if p is None:
            return
        r = self._rect
        if r.width() < 2 or r.height() < 2:
            return
        left, top, w, h = r.left(), r.top(), r.width(), r.height()
        p.setPen(_dotted(DIVISION))
        for i in range(1, DIVISIONS_X):
            x = left + w * i / DIVISIONS_X
            p.drawLine(QtCore.QLineF(x, top, x, top + h))
        for value in self._y_ticks():
            y = self._y_px(value)
            if top < y < top + h:
                p.setPen(_dotted(ZERO if value == 0 else DIVISION))
                p.drawLine(QtCore.QLineF(left, y, left + w, y))
        # The centre crosshair with minor ticks, as on a scope's screen.
        cx, cy = left + w / 2, top + h / 2
        p.setPen(_solid(CROSSHAIR))
        p.drawLine(QtCore.QLineF(left, cy, left + w, cy))
        p.drawLine(QtCore.QLineF(cx, top, cx, top + h))
        for i in range(1, DIVISIONS_X * MINOR_PER_DIVISION):
            x = left + w * i / (DIVISIONS_X * MINOR_PER_DIVISION)
            p.drawLine(QtCore.QLineF(x, cy - MINOR_TICK_PX, x, cy + MINOR_TICK_PX))
        for i in range(1, DIVISIONS_Y * MINOR_PER_DIVISION):
            y = top + h * i / (DIVISIONS_Y * MINOR_PER_DIVISION)
            p.drawLine(QtCore.QLineF(cx - MINOR_TICK_PX, y, cx + MINOR_TICK_PX, y))
        p.setPen(_solid(FRAME))
        p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))


class LaneMarkers(_ViewItem):
    """Trigger `T` markers and cursor `A`/`B` flags; each lane has one, drawn on top."""

    def __init__(self, vb: pg.ViewBox) -> None:
        super().__init__(vb, z=500.0)  # over the traces, under the cursor lines' handles
        self.trigger_level: float | None = None
        self.trigger_time: float | None = None
        self.cursor_a: float | None = None
        self.cursor_b: float | None = None
        self.flags = False  # the top lane carries the flags

    def set_state(
        self,
        *,
        trigger_level: float | None,
        trigger_time: float | None,
        cursor_a: float | None,
        cursor_b: float | None,
        flags: bool,
    ) -> None:
        state = (trigger_level, trigger_time, cursor_a, cursor_b, flags)
        if state == (
            self.trigger_level,
            self.trigger_time,
            self.cursor_a,
            self.cursor_b,
            self.flags,
        ):
            return
        self.trigger_level, self.trigger_time, self.cursor_a, self.cursor_b, self.flags = state
        self.update()

    @staticmethod
    def _font() -> QtGui.QFont:
        font = QtGui.QFont(UI_FAMILY)
        font.setPixelSize(10)
        font.setBold(True)
        return font

    def _box(
        self, p: QtGui.QPainter, box: QtCore.QRectF, letter: str, color: str, filled: bool
    ) -> None:
        if filled:
            p.fillRect(box, QtGui.QColor(color))
            p.setPen(QtGui.QColor("#000000"))
        else:
            p.fillRect(box, QtGui.QColor("#000000"))
            p.setPen(_solid(color))
            p.drawRect(box.adjusted(0.5, 0.5, -0.5, -0.5))
            p.setPen(QtGui.QColor(color))
        p.drawText(box, QtCore.Qt.AlignmentFlag.AlignCenter, letter)

    def paint(
        self,
        p: QtGui.QPainter | None,
        _option: QtWidgets.QStyleOptionGraphicsItem | None = None,
        _widget: QtWidgets.QWidget | None = None,
    ) -> None:
        if p is None:
            return
        r = self._rect
        left, top, w, h = r.left(), r.top(), r.width(), r.height()
        p.setFont(self._font())
        if self.cursor_a is not None and self.cursor_b is not None:
            xa, xb = self._x_px(self.cursor_a), self._x_px(self.cursor_b)
            shade = QtGui.QColor(CURSOR)
            shade.setAlpha(18)
            x0, x1 = max(min(xa, xb), left), min(max(xa, xb), left + w)
            if x1 > x0:
                p.fillRect(QtCore.QRectF(x0, top, x1 - x0, h), shade)
        if self.trigger_level is not None:
            y = self._y_px(self.trigger_level)
            if top <= y <= top + h:
                box = QtCore.QRectF(left + w - FLAG_PX - 1, y - FLAG_PX / 2, FLAG_PX, FLAG_PX)
                self._box(p, box, "T", TRIGGER, filled=True)
        if self.trigger_time is not None:
            x = self._x_px(self.trigger_time)
            if left <= x <= left + w:
                box = QtCore.QRectF(x - FLAG_PX / 2, top + 1, FLAG_PX, FLAG_PX)
                self._box(p, box, "T", TRIGGER, filled=True)
        if self.flags:
            # One row below the trigger's T; A right of its line, B left, so all three
            # stay readable when they coincide (right after a capture).
            row = top + FLAG_PX + 3
            for value, letter, filled, dx in (
                (self.cursor_b, "B", False, -FLAG_PX - 1),
                (self.cursor_a, "A", True, 1),
            ):
                if value is None:
                    continue
                x = self._x_px(value)
                if left <= x <= left + w:
                    box = QtCore.QRectF(x + dx, row, FLAG_PX, FLAG_PX)
                    self._box(p, box, letter, CURSOR, filled)
