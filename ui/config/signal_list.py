"""
The editor's signal list (R11.3): the Signals pane's layout, with where each signal comes
from.

Lanes as small-caps headers with a hairline, one row per left/right pair
(`signal_rows.pair_rows`), columns Signal | L | R (one column when no row is a pair). A
side's cell is its swatch (filled = shown when the stream opens, hollow = hidden), its field
and its byte (a text value's position). Fields without a signal come last under
`NOT PLOTTED`, the X axis tagged.

The list only reports: `picked` (a signal id, or `FIELD_MARK` + a field) when a cell or row
is pressed, `toggled` (a signal id) when a swatch is pressed, and `dropped` (from
`SignalTree`) when a row is dragged onto a lane. The editor applies them to the draft and
calls `show` again; selecting only repaints, so an item is never freed while Qt's mouse
handler still holds it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from PyQt6 import QtCore, QtGui, QtWidgets

from core.config.draft import FieldSlot
from styles import (
    BORDER,
    BORDER_DIM,
    NUMBER_FAMILY,
    TEXT,
    TEXT_BRIGHT,
    TEXT_DIM,
    TEXT_DISABLED,
    TEXT_MUTED,
    mono_font,
)
from ui.charts.lanes import LaneSpec
from ui.panels.signal_rows import lane_title, pair_rows
from ui.panels.signals import (
    DEFAULT_LANE_LABEL,
    NAME_COLUMN,
    ROLE_LANE,
    ROLE_SIGNAL,
    SWATCH_PX,
    SignalDelegate,
    SignalTree,
    draw_swatch,
)

ROLE_FIELD = QtCore.Qt.ItemDataRole.UserRole + 2
ROLE_CELL = QtCore.Qt.ItemDataRole.UserRole + 4  # a side's Cell
ROLE_ON = QtCore.Qt.ItemDataRole.UserRole + 5  # a row: any of its signals shown
UNPLOTTED_LANE = "__unplotted__"
FIELD_MARK = "field:"  # ROLE_SIGNAL of a row for a field without a signal
SIDE_WIDTH = 250
SELECTED_FILL = "#262626"
PAD = 12  # the list's left margin, as the canvas and the Signals pane


@dataclass(frozen=True)
class Cell:
    """One side of a row: a signal (with its swatch) or a field without one."""

    key: str  # signal id, or FIELD_MARK + field
    field: str
    where: str  # byte offset, or a text value's position
    color: str | None = None  # None: not plotted, no swatch
    shown: bool = True
    dashed: bool = False


class CellDelegate(SignalDelegate):
    """Lane headers as the Signals pane draws them; sides as swatch, field and byte."""

    def __init__(self, tree: FieldTree) -> None:
        super().__init__(tree)
        self._tree = tree

    def paint(
        self,
        painter: QtGui.QPainter | None,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:
        cell = index.data(ROLE_CELL)
        if painter is None or not isinstance(cell, Cell):
            if painter is not None and index.column() == NAME_COLUMN:
                self._paint_name(painter, option, index)
                return
            super().paint(painter, option, index)
            return
        rect = QtCore.QRectF(option.rect)
        painter.save()
        selected = cell.key == self._tree.selected
        if selected:
            painter.fillRect(rect, QtGui.QColor(SELECTED_FILL))
            bar = QtGui.QColor(cell.color or TEXT)
            painter.fillRect(QtCore.QRectF(rect.left(), rect.top(), 2, rect.height()), bar)
        x = rect.left() + 8
        if cell.color is not None:
            box = QtCore.QRectF(x, rect.center().y() - SWATCH_PX / 2, SWATCH_PX, SWATCH_PX)
            draw_swatch(painter, box, cell.color, cell.shown, cell.dashed)
        x += SWATCH_PX + 8
        font = mono_font()
        font.setPixelSize(11)
        painter.setFont(font)
        if selected:
            color = TEXT_BRIGHT
        elif cell.color is None:
            color = TEXT_MUTED
        else:
            color = TEXT_DIM if cell.shown else TEXT_DISABLED
        painter.setPen(QtGui.QColor(color))
        where_w = 30
        name_rect = QtCore.QRectF(x, rect.top(), rect.right() - x - where_w - 8, rect.height())
        flags = QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
        elided = QtGui.QFontMetrics(font).elidedText(
            cell.field, QtCore.Qt.TextElideMode.ElideRight, int(name_rect.width())
        )
        painter.drawText(name_rect, flags, elided)
        number = QtGui.QFont(NUMBER_FAMILY)
        number.setPixelSize(11)
        painter.setFont(number)
        painter.setPen(QtGui.QColor(TEXT_DISABLED))
        where_rect = QtCore.QRectF(rect.right() - where_w - 8, rect.top(), where_w, rect.height())
        right = QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        painter.drawText(where_rect, right, cell.where)
        painter.restore()

    def _paint_name(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:
        if isinstance(index.data(ROLE_LANE), str):
            padded = QtWidgets.QStyleOptionViewItem(option)
            padded.rect = option.rect.adjusted(PAD, 0, 0, 0)
            super().paint(painter, padded, index)
            return
        painter.save()
        on = bool(index.data(ROLE_ON))
        painter.setPen(QtGui.QColor(TEXT if on else TEXT_DISABLED))
        painter.setFont(option.font)
        rect = QtCore.QRectF(option.rect).adjusted(PAD, 0, -4, 0)
        flags = QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
        painter.drawText(rect, flags, str(index.data() or ""))
        painter.restore()


class FieldTree(SignalTree):
    picked = QtCore.pyqtSignal(str)  # signal id, or FIELD_MARK + field
    toggled = QtCore.pyqtSignal(str)  # signal id: its swatch was pressed

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.selected: str | None = None
        self._text = False
        self._pressing = False  # a press picks its cell itself, not the row's first side
        self.setColumnCount(3)
        self.setRootIsDecorated(False)
        self.setIndentation(0)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.setItemDelegate(CellDelegate(self))
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.setStyleSheet(
            "QTreeWidget { border: none; }"
            f" QHeaderView::section {{ color: {TEXT_MUTED}; background: transparent;"
            f" border: none; border-bottom: 1px solid {BORDER_DIM}; padding: 4px 12px; }}"
            f" QTreeWidget::item:hover {{ background: transparent; }}"
            f" QTreeWidget {{ selection-background-color: {BORDER}; }}"
        )
        header = self.header()
        assert header is not None
        header.setStretchLastSection(False)
        header.setSectionsClickable(False)
        header.setSectionResizeMode(NAME_COLUMN, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for col in (1, 2):
            header.setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeMode.Fixed)
            header.resizeSection(col, SIDE_WIDTH)
        self.currentItemChanged.connect(self._on_current)

    # --- content ---

    def show_stream(
        self,
        specs: list[LaneSpec],
        assignment: Mapping[str, str],
        signals: Mapping[str, Mapping[str, Any]],
        slots: list[FieldSlot],
        time_field: str | None,
        text: bool,
    ) -> None:
        """Rebuilds the list: lanes and their pair rows, then the fields not plotted."""
        self._text = text
        self.blockSignals(True)
        self.clear()
        by_name = {s.name: s for s in slots}
        rows_by_lane = {
            spec.key: pair_rows([s for s, lane in assignment.items() if lane == spec.key], signals)
            for spec in specs
        }
        paired = any(r.left and r.right for rows in rows_by_lane.values() for r in rows)
        side = "Value" if text else "Field"
        self.setHeaderLabels(["Signal", "L", "R"] if paired else ["Signal", side, ""])
        self.setColumnHidden(2, not paired)
        for spec in specs:
            label = spec.label or DEFAULT_LANE_LABEL
            self._add_lane(spec.key, lane_title(label), label)
            lane_item = self.topLevelItem(self.topLevelItemCount() - 1)
            assert lane_item is not None
            for row in rows_by_lane[spec.key]:
                item = QtWidgets.QTreeWidgetItem([row.name, "", ""])
                item.setData(0, ROLE_SIGNAL, row.members[0])
                item.setData(0, ROLE_FIELD, str(signals[row.members[0]].get("field", "")))
                item.setData(0, ROLE_ON, any(signals[s].get("visible", True) for s in row.members))
                item.setFlags(
                    QtCore.Qt.ItemFlag.ItemIsEnabled
                    | QtCore.Qt.ItemFlag.ItemIsDragEnabled
                    | QtCore.Qt.ItemFlag.ItemIsDropEnabled
                )
                sides = [row.left, row.right] if paired else [row.members[0]]
                for col, sid in enumerate(sides, start=1):
                    if sid is None:
                        continue
                    sig = signals[sid]
                    field = str(sig.get("field", ""))
                    slot = by_name.get(field)
                    line = sig.get("line")
                    style = line.get("style", "solid") if isinstance(line, dict) else "solid"
                    cell = Cell(
                        sid,
                        field,
                        self._where(slot),
                        str(sig.get("color", "#FFFFFF")),
                        bool(sig.get("visible", True)),
                        style != "solid",
                    )
                    item.setData(col, ROLE_CELL, cell)
                    item.setData(col, ROLE_SIGNAL, sid)
                    item.setText(col, field)
                    item.setToolTip(
                        col, f"{sig.get('label', sid)}\nSwatch: shown when the stream opens"
                    )
                lane_item.addChild(item)
            lane_item.setExpanded(True)

        plotted = {str(s.get("field")) for s in signals.values()}
        loose = [s for s in slots if s.name not in plotted]
        if loose:
            self._add_lane(UNPLOTTED_LANE, "NOT PLOTTED", "Drag a field onto a lane to plot it")
            lane_item = self.topLevelItem(self.topLevelItemCount() - 1)
            assert lane_item is not None
            for slot in loose:
                tag = "X axis" if slot.name == time_field else ""
                item = QtWidgets.QTreeWidgetItem([tag, slot.name, ""])
                key = FIELD_MARK + slot.name
                item.setData(0, ROLE_SIGNAL, key)
                item.setData(0, ROLE_FIELD, slot.name)
                item.setData(1, ROLE_CELL, Cell(key, slot.name, self._where(slot)))
                item.setFlags(
                    QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsDragEnabled
                )
                lane_item.addChild(item)
            lane_item.setExpanded(True)
        self.blockSignals(False)
        self._show_current()

    def _add_lane(self, key: str, title: str, tooltip: str) -> None:
        item = QtWidgets.QTreeWidgetItem([title, "", ""])
        item.setData(0, ROLE_LANE, key)
        item.setToolTip(0, tooltip)
        item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsDropEnabled)
        self.addTopLevelItem(item)
        item.setFirstColumnSpanned(True)

    def _where(self, slot: FieldSlot | None) -> str:
        if slot is None:
            return ""
        return str(slot.index + 1) if self._text else str(slot.offset)

    # --- selection ---

    def select(self, key: str | None) -> None:
        """Lights the cell of `key` (a signal, or FIELD_MARK + field); repaints only."""
        self.selected = key
        self._show_current()
        viewport = self.viewport()
        if viewport is not None:
            viewport.update()

    def item_of(self, key: str) -> QtWidgets.QTreeWidgetItem | None:
        """The row holding `key`."""
        for i in range(self.topLevelItemCount()):
            lane = self.topLevelItem(i)
            for j in range(lane.childCount() if lane is not None else 0):
                child = lane.child(j) if lane is not None else None
                if child is not None and key in self._keys(child):
                    return child
        return None

    @staticmethod
    def _keys(item: QtWidgets.QTreeWidgetItem) -> list[str]:
        keys = [item.data(col, ROLE_SIGNAL) for col in range(3)]
        return [k for k in keys if isinstance(k, str)]

    def _show_current(self) -> None:
        item = self.item_of(self.selected) if self.selected is not None else None
        if item is not None and item is not self.currentItem():
            self.blockSignals(True)
            self.setCurrentItem(item)
            self.blockSignals(False)
            self.scrollToItem(item)

    def _on_current(self, current: QtWidgets.QTreeWidgetItem | None, _previous: Any) -> None:
        """Keyboard moves: a row that doesn't hold the selection picks its first side."""
        if self._pressing or current is None or isinstance(current.data(0, ROLE_LANE), str):
            return
        keys = self._keys(current)
        if keys and self.selected not in keys:
            self.picked.emit(keys[0])

    # --- input ---

    def cell_at(self, pos: QtCore.QPoint) -> tuple[str | None, bool]:
        """(key under `pos`, whether it's on the swatch)."""
        index = self.indexAt(pos)
        if not index.isValid() or isinstance(index.data(ROLE_LANE), str):
            return None, False
        cell = index.data(ROLE_CELL)
        if isinstance(cell, Cell):
            left = self.visualRect(index).left()
            on_swatch = cell.color is not None and pos.x() < left + 8 + SWATCH_PX + 4
            return cell.key, on_swatch
        item = self.itemFromIndex(index)
        keys = self._keys(item) if item is not None else []
        return (keys[0] if keys else None), False

    def mousePressEvent(self, event: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if event is not None:
            key, on_swatch = self.cell_at(event.position().toPoint())
            if key is not None and event.button() == QtCore.Qt.MouseButton.LeftButton:
                if on_swatch:
                    self.toggled.emit(key)
                elif key != self.selected:
                    self.picked.emit(key)
            elif key is not None and event.button() == QtCore.Qt.MouseButton.RightButton:
                if key != self.selected:
                    self.picked.emit(key)
        self._pressing = True
        try:
            super().mousePressEvent(event)
        finally:
            self._pressing = False
