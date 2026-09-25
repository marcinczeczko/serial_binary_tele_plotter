"""
The Signals pane (R3.1, R6.2, R9.4): legend, visibility, lanes and the cursor readout in one
list, drawn as a scope draws its channel list.

Signals are grouped by the lane they're drawn in, in lane order, under a small-caps header
(`SPEED rps`). A left/right pair is one row (`signal_rows.pair_rows`): its name, then an L
and an R swatch in the traces' colours. The swatch is the toggle: filled = shown, hollow =
hidden; a dashed trace's swatch has a gap in the middle. With a cursor on the plot, each
shown swatch gets its value at A in the trace's colour (Δ to B in the tooltip), under a
boxed `A … B … ΔT …` header. The filter appears on Ctrl+F or when you type in the pane.

Drag a row onto another lane (or below the list, for a new lane), or right-click it to
move it; both signals of a pair move. Moves and visibility last across runs as view
overrides (R5.3); the stream editor's Visible and Lane columns change streams.json itself.
"""

from __future__ import annotations

import math

from PyQt6 import QtCore, QtGui, QtWidgets

from core.types import StreamConfig
from styles import BORDER, BUTTON_BORDER, NUMBER_FAMILY, TEXT, TEXT_DIM, TEXT_DISABLED, TEXT_MUTED
from ui.charts.lanes import lane_layout
from ui.charts.series import Readout
from ui.common.numbers import format_significant
from ui.panels.signal_rows import SignalRow, lane_title, pair_rows

NEW_LANE = "__new__"
DEFAULT_LANE_LABEL = "Main"
ROLE_SIGNAL = QtCore.Qt.ItemDataRole.UserRole
ROLE_LANE = QtCore.Qt.ItemDataRole.UserRole + 1
ROLE_SWATCH = QtCore.Qt.ItemDataRole.UserRole + 3  # (color, shown, dashed) of a column's signal
NAME_COLUMN, LEFT_COLUMN, RIGHT_COLUMN = 0, 1, 2
SIDE_COLUMNS = {"left": LEFT_COLUMN, "right": RIGHT_COLUMN}
SWATCH_PX = 12
VALUE_MIN_PX = 28  # a value column without a value: the swatch and its margins


def draw_swatch(
    painter: QtGui.QPainter, rect: QtCore.QRectF, color: str, shown: bool, dashed: bool
) -> None:
    """A 12 px square: filled when shown, hollow grey when hidden; dashed: split in two."""
    if not shown:
        painter.setPen(QtGui.QColor(TEXT_DISABLED))
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        return
    fill = QtGui.QColor(color)
    if dashed:
        half = (rect.width() - 2) / 2
        painter.fillRect(QtCore.QRectF(rect.left(), rect.top(), half, rect.height()), fill)
        painter.fillRect(QtCore.QRectF(rect.right() - half, rect.top(), half, rect.height()), fill)
    else:
        painter.fillRect(rect, fill)


class SignalDelegate(QtWidgets.QStyledItemDelegate):
    """Draws lane headers (small caps and a hairline) and the swatch columns."""

    def paint(
        self,
        painter: QtGui.QPainter | None,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:
        if painter is None:
            return
        rect = QtCore.QRectF(option.rect)
        if isinstance(index.data(ROLE_LANE), str):
            if index.column() != NAME_COLUMN:
                return
            painter.save()
            font = QtGui.QFont(option.font)
            font.setPixelSize(10)
            font.setBold(True)
            font.setLetterSpacing(QtGui.QFont.SpacingType.PercentageSpacing, 110)
            painter.setFont(font)
            painter.setPen(QtGui.QColor(TEXT_MUTED))
            text = str(index.data())
            flags = QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
            painter.drawText(rect, flags, text)
            end = rect.left() + QtGui.QFontMetrics(font).horizontalAdvance(text) + 8
            view = getattr(option, "widget", None)  # a lane row spans the columns
            right = view.width() - 6 if isinstance(view, QtWidgets.QWidget) else rect.right()
            painter.fillRect(
                QtCore.QRectF(end, rect.center().y(), right - end, 1), QtGui.QColor(BORDER)
            )
            painter.restore()
            return
        swatch = index.data(ROLE_SWATCH)
        if index.column() in (LEFT_COLUMN, RIGHT_COLUMN):
            if not isinstance(swatch, tuple):
                return
            color, shown, dashed = swatch
            painter.save()
            box = QtCore.QRectF(
                rect.left() + 4, rect.center().y() - SWATCH_PX / 2, SWATCH_PX, SWATCH_PX
            )
            draw_swatch(painter, box, color, shown, dashed)
            text = str(index.data() or "")
            if text:
                font = QtGui.QFont(NUMBER_FAMILY)
                font.setPixelSize(11)
                painter.setFont(font)
                painter.setPen(QtGui.QColor(color))
                text_rect = rect.adjusted(SWATCH_PX + 9, 0, -2, 0)
                flags = QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
                painter.drawText(text_rect, flags, text)
            painter.restore()
            return
        super().paint(painter, option, index)

    def sizeHint(  # noqa: N802
        self, option: QtWidgets.QStyleOptionViewItem, index: QtCore.QModelIndex
    ) -> QtCore.QSize:
        hint = super().sizeHint(option, index)
        if index.column() in (LEFT_COLUMN, RIGHT_COLUMN):
            text = str(index.data() or "")
            font = QtGui.QFont(NUMBER_FAMILY)
            font.setPixelSize(11)
            width = (
                SWATCH_PX
                + 10
                + (QtGui.QFontMetrics(font).horizontalAdvance(text) + 6 if text else 0)
            )
            return QtCore.QSize(max(width, 26), max(hint.height(), 20))
        return QtCore.QSize(hint.width(), max(hint.height(), 20))


class SignalTree(QtWidgets.QTreeWidget):
    """
    The tree, with drag and drop between lanes. A drop only reports where a signal should
    go (`dropped`); the panel moves it and rebuilds, so Qt never rearranges items itself.
    """

    dropped = QtCore.pyqtSignal(str, str)  # signal id, lane key (NEW_LANE: empty space)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(QtCore.Qt.DropAction.MoveAction)
        # A drop is applied after the drag has finished: the move rebuilds the tree, which
        # mustn't happen while Qt's drag loop still holds its items.
        self._pending_drop: tuple[str, str] | None = None
        self._drop_timer = QtCore.QTimer(self)
        self._drop_timer.setSingleShot(True)
        self._drop_timer.setInterval(0)
        self._drop_timer.timeout.connect(self._apply_drop)

    def drop_target(self, pos: QtCore.QPoint) -> str | None:
        """The lane a drop at `pos` means: a lane row, a signal's lane, or a new lane."""
        item = self.itemAt(pos)
        if item is None:
            return NEW_LANE
        lane = item.data(0, ROLE_LANE)
        if isinstance(lane, str):
            return lane
        parent = item.parent()
        lane = parent.data(0, ROLE_LANE) if parent is not None else None
        return lane if isinstance(lane, str) else None

    def dropEvent(self, event: QtGui.QDropEvent | None) -> None:  # noqa: N802
        if event is None:
            return
        item = self.currentItem()
        sid = item.data(0, ROLE_SIGNAL) if item is not None else None
        target = self.drop_target(event.position().toPoint())
        event.setDropAction(QtCore.Qt.DropAction.IgnoreAction)
        event.accept()
        if isinstance(sid, str) and target is not None:
            self._pending_drop = (sid, target)
            self._drop_timer.start()

    def _apply_drop(self) -> None:
        if self._pending_drop is not None:
            sid, target = self._pending_drop
            self._pending_drop = None
            self.dropped.emit(sid, target)


class FilterEdit(QtWidgets.QLineEdit):
    """The filter: hidden until Ctrl+F or typing in the pane; Esc clears and hides it."""

    def keyPressEvent(self, event: QtGui.QKeyEvent | None) -> None:  # noqa: N802
        if event is not None and event.key() == QtCore.Qt.Key.Key_Escape:
            self.clear()
            self.hide()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event: QtGui.QFocusEvent | None) -> None:  # noqa: N802
        super().focusOutEvent(event)
        if not self.text():
            self.hide()


class SignalListPanel(QtWidgets.QWidget):
    """
    Signals grouped by lane, one row per pair. The owner applies what it emits (plot,
    persistence) and rebuilds the list for another stream with `rebuild_list`.
    """

    signal_visibility_changed = QtCore.pyqtSignal(str, bool)
    signal_lane_changed = QtCore.pyqtSignal(str, str, str)  # signal id, lane key, lane label

    def __init__(self) -> None:
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.cursor_lbl = QtWidgets.QLabel("")  # A / B / ΔT, boxed, while a cursor is set
        self.cursor_lbl.setStyleSheet(
            f"color: {TEXT}; border: 1px solid {BUTTON_BORDER}; padding: 3px 6px;"
            f" margin: 6px 6px 0 6px; font-family: '{NUMBER_FAMILY}'; font-size: 11px;"
        )
        self.cursor_lbl.hide()
        self.cursor_lbl.setWordWrap(True)
        # The header never widens the pane; the pane's width is the user's (R9.3).
        self.cursor_lbl.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred
        )
        layout.addWidget(self.cursor_lbl)

        self.filter_edit = FilterEdit()
        self.filter_edit.setPlaceholderText("Filter signals")
        self.filter_edit.textChanged.connect(self._apply_filter)
        self.filter_edit.hide()
        layout.addWidget(self.filter_edit)
        find = QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Find, self)
        find.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        find.activated.connect(self.show_filter)

        self.tree = SignalTree()
        self.tree.dropped.connect(self._move_row_of)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Signals", "L", "R"])
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(8)
        self.tree.setUniformRowHeights(True)
        self.tree.setItemDelegate(SignalDelegate(self.tree))
        self.tree.setStyleSheet(
            "QTreeWidget { border: none; }"
            f" QHeaderView::section {{ color: {TEXT_DIM}; border: none;"
            f" border-bottom: 1px solid {BORDER}; padding: 4px 6px; }}"
        )
        self.tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.installEventFilter(self)
        hdr = self.tree.header()
        assert hdr is not None
        hdr.setStretchLastSection(False)
        hdr.setSectionsClickable(False)
        hdr.setSectionResizeMode(NAME_COLUMN, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for col in (LEFT_COLUMN, RIGHT_COLUMN):
            hdr.setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeMode.Fixed)
            hdr.resizeSection(col, VALUE_MIN_PX)
        layout.addWidget(self.tree, 1)

        self._cfg: StreamConfig = {"signals": {}}
        self._lanes: list[tuple[str, str]] = []  # (key, label), in display order
        self._assignment: dict[str, str] = {}  # signal -> lane key
        self._visible: dict[str, bool] = {}
        self._rows: dict[str, SignalRow] = {}  # signal -> its row
        self._items: dict[str, QtWidgets.QTreeWidgetItem] = {}  # signal -> its row's item
        self._lane_items: dict[str, QtWidgets.QTreeWidgetItem] = {}
        self._values: dict[str, str] = {}  # signal -> its value at A, as shown
        self._deltas: dict[str, float] = {}  # signal -> its value at A minus at B

    # --- building ---

    def rebuild_list(self, cfg: StreamConfig) -> None:
        """Shows a stream's signals, grouped by its lanes, pairs on one row."""
        self._cfg = cfg
        specs, assignment = lane_layout(cfg)
        self._lanes = [(s.key, s.label or DEFAULT_LANE_LABEL) for s in specs]
        self._assignment = dict(assignment)
        self._visible = {
            sid: bool(sig.get("visible", True)) for sid, sig in cfg.get("signals", {}).items()
        }
        self.show_readout(None)
        self._rebuild_tree()

    def _rebuild_tree(self) -> None:
        self.tree.clear()
        self._items.clear()
        self._rows.clear()
        self._lane_items.clear()
        signals = self._cfg.get("signals", {})
        for key, label in self._lanes:
            lane_item = QtWidgets.QTreeWidgetItem([lane_title(label), "", ""])
            lane_item.setData(0, ROLE_LANE, key)
            lane_item.setToolTip(0, label)
            lane_item.setFlags(
                QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsDropEnabled
            )
            self.tree.addTopLevelItem(lane_item)
            lane_item.setFirstColumnSpanned(True)  # only works once it's in the tree
            self._lane_items[key] = lane_item
            members = [sid for sid in signals if self._assignment.get(sid) == key]
            for row in pair_rows(members, signals):
                item = QtWidgets.QTreeWidgetItem([row.name, "", ""])
                item.setData(0, ROLE_SIGNAL, row.members[0])
                item.setToolTip(0, self._row_tooltip(row))
                item.setFlags(
                    QtCore.Qt.ItemFlag.ItemIsEnabled
                    | QtCore.Qt.ItemFlag.ItemIsSelectable
                    | QtCore.Qt.ItemFlag.ItemIsDragEnabled
                    | QtCore.Qt.ItemFlag.ItemIsDropEnabled  # a drop on a signal: its lane
                )
                lane_item.addChild(item)
                for sid in row.members:
                    self._rows[sid] = row
                    self._items[sid] = item
                self._paint_row(row)
            lane_item.setExpanded(True)
        self._apply_filter(self.filter_edit.text())

    def _row_tooltip(self, row: SignalRow) -> str:
        signals = self._cfg.get("signals", {})
        parts = [
            f"{signals[sid].get('label', sid)} ({signals[sid].get('field', sid)})"
            for sid in row.members
        ]
        return "\n".join(parts) + "\nClick a swatch to show or hide; drag to another lane"

    def _column(self, sid: str) -> int:
        row = self._rows[sid]
        return RIGHT_COLUMN if sid == row.right else LEFT_COLUMN

    def _paint_row(self, row: SignalRow) -> None:
        signals = self._cfg.get("signals", {})
        for sid in row.members:
            item = self._items[sid]
            col = self._column(sid)
            sig = signals.get(sid, {})
            dashed = sig.get("line", {}).get("style", "solid") != "solid"
            item.setData(col, ROLE_SIGNAL, sid)
            item.setData(
                col,
                ROLE_SWATCH,
                (str(sig.get("color", "#FFFFFF")), self._visible.get(sid, True), dashed),
            )
            item.setText(col, self._values.get(sid, ""))
            item.setToolTip(col, self._swatch_tooltip(sid))

    def _swatch_tooltip(self, sid: str) -> str:
        label = self._cfg.get("signals", {}).get(sid, {}).get("label", sid)
        state = "shown" if self._visible.get(sid, True) else "hidden"
        tip = f"{label}: {state} (click to toggle)"
        delta = self._deltas.get(sid)
        if delta is not None and math.isfinite(delta):
            tip += f"\nΔ (A − B) {format_significant(delta, sign=True)}"
        return tip

    # --- filter ---

    def show_filter(self, text: str = "") -> None:
        self.filter_edit.show()
        self.filter_edit.setFocus()
        if text:
            self.filter_edit.insert(text)

    def eventFilter(self, obj: QtCore.QObject | None, event: QtCore.QEvent | None) -> bool:  # noqa: N802
        """Typing in the list starts the filter with what was typed."""
        if obj is self.tree and isinstance(event, QtGui.QKeyEvent):
            if event.type() == QtCore.QEvent.Type.KeyPress:
                text = event.text()
                plain = event.modifiers() in (
                    QtCore.Qt.KeyboardModifier.NoModifier,
                    QtCore.Qt.KeyboardModifier.ShiftModifier,
                )
                if plain and text and text.isprintable() and not text.isspace():
                    self.show_filter(text)
                    return True
        return super().eventFilter(obj, event)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        signals = self._cfg.get("signals", {})
        for lane_item in self._lane_items.values():
            any_shown = False
            for i in range(lane_item.childCount()):
                child = lane_item.child(i)
                if child is None:
                    continue
                row = self._rows.get(str(child.data(0, ROLE_SIGNAL)))
                names = [child.text(0).lower()]
                if row is not None:
                    names += [s.lower() for s in row.members]
                    names += [str(signals.get(s, {}).get("label", "")).lower() for s in row.members]
                match = not needle or any(needle in n for n in names)
                child.setHidden(not match)
                any_shown |= match
            lane_item.setHidden(not any_shown)

    # --- visibility ---

    def _on_item_clicked(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        sid = item.data(column, ROLE_SIGNAL) if column in (LEFT_COLUMN, RIGHT_COLUMN) else None
        if isinstance(sid, str):
            self.set_visible(sid, not self._visible.get(sid, True))

    def is_visible(self, sid: str) -> bool:
        return self._visible.get(sid, False)

    def set_visible(self, sid: str, visible: bool) -> None:
        """Shows or hides a signal as its swatch does (emits the change)."""
        if sid not in self._rows or self._visible.get(sid) == visible:
            return
        self._visible[sid] = visible
        self._paint_row(self._rows[sid])
        self.signal_visibility_changed.emit(sid, visible)

    def set_lane_visible(self, lane: str, visible: bool) -> None:
        """Shows or hides every signal of a lane (its header's context menu)."""
        for sid, key in list(self._assignment.items()):
            if key == lane:
                self.set_visible(sid, visible)

    # --- lanes ---

    def lanes(self) -> list[tuple[str, str]]:
        return list(self._lanes)

    def lane_of(self, sid: str) -> str | None:
        return self._assignment.get(sid)

    def row_of(self, sid: str) -> SignalRow | None:
        return self._rows.get(sid)

    def rows(self) -> list[SignalRow]:
        """Every row, in display order."""
        seen: list[SignalRow] = []
        for key, _ in self._lanes:
            lane_item = self._lane_items.get(key)
            if lane_item is None:
                continue
            for i in range(lane_item.childCount()):
                child = lane_item.child(i)
                row = self._rows.get(str(child.data(0, ROLE_SIGNAL))) if child else None
                if row is not None:
                    seen.append(row)
        return seen

    def _new_lane(self) -> str:
        n = len(self._lanes) + 1
        taken = {k for k, _ in self._lanes}
        while f"Lane {n}" in taken:
            n += 1
        lane = f"Lane {n}"
        self._lanes.append((lane, lane))
        return lane

    def move_to_lane(self, sid: str, lane: str) -> None:
        """Moves one signal to a lane; `NEW_LANE` creates one ("Lane N")."""
        self._move([sid], lane)

    def _move_row_of(self, sid: str, lane: str) -> None:
        """A row dragged or moved from its menu: both signals of a pair go."""
        row = self._rows.get(sid)
        self._move(row.members if row is not None else [sid], lane)

    def _move(self, sids: list[str], lane: str) -> None:
        sids = [s for s in sids if s in self._assignment]
        if not sids:
            return
        if lane == NEW_LANE:
            lane = self._new_lane()
        moving = [s for s in sids if self._assignment[s] != lane]
        if not moving:
            return
        for sid in moving:
            self._assignment[sid] = lane
        label = next((lbl for k, lbl in self._lanes if k == lane), lane)
        # A lane left without signals disappears from the list, as from the plot.
        used = set(self._assignment.values())
        self._lanes = [(k, lbl) for k, lbl in self._lanes if k in used]
        self._rebuild_tree()
        for sid in moving:
            self.signal_lane_changed.emit(sid, lane, label)

    def _on_context_menu(self, pos: QtCore.QPoint) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        menu = QtWidgets.QMenu(self)
        lane = item.data(0, ROLE_LANE)
        if isinstance(lane, str):
            show = menu.addAction("Show all in this lane")
            hide = menu.addAction("Hide all in this lane")
            assert show is not None and hide is not None
            show.triggered.connect(lambda: self.set_lane_visible(lane, True))
            hide.triggered.connect(lambda: self.set_lane_visible(lane, False))
        else:
            sid = item.data(0, ROLE_SIGNAL)
            if not isinstance(sid, str):
                return
            move = menu.addMenu("Move to lane")
            assert move is not None
            for key, label in self._lanes:
                act = move.addAction(label)
                assert act is not None
                act.setEnabled(key != self._assignment.get(sid))
                act.triggered.connect(lambda _=False, k=key: self._move_row_of(sid, k))
            move.addSeparator()
            new = move.addAction("New lane")
            assert new is not None
            new.triggered.connect(lambda: self._move_row_of(sid, NEW_LANE))
        viewport = self.tree.viewport()
        assert viewport is not None
        menu.exec(viewport.mapToGlobal(pos))

    # --- readout ---

    def show_readout(self, readout: Readout | None) -> None:
        """
        Each shown signal's value at A (the cursor) beside its swatch, in its colour; the
        header gives A, B (the anchor) and ΔT = B − A. Δ to B is in the swatch's tooltip.
        """
        self._values = {}
        self._deltas = {}
        if readout is None:
            self.cursor_lbl.hide()
            self.cursor_lbl.setText("")
        else:
            header = f"A {format_significant(readout.t)}s"
            if readout.dt is not None:
                b = readout.t - readout.dt
                header += (
                    f"  B {format_significant(b)}s"
                    f"  ΔT {format_significant(-readout.dt, sign=True)}s"
                )
            self.cursor_lbl.setText(header)
            self.cursor_lbl.show()
            for sid, value in readout.values.items():
                if self._visible.get(sid, True):
                    self._values[sid] = format_significant(value) if math.isfinite(value) else "n/a"
            self._deltas = dict(readout.deltas)
        for row in {id(r): r for r in self._rows.values()}.values():
            self._paint_row(row)
        self._fit_value_columns(readout is not None)

    def _fit_value_columns(self, keep_width: bool) -> None:
        """
        The value columns grow to fit what they hold and don't shrink while a cursor is
        set, so they don't jitter as it moves; without one, they're just the swatches.
        """
        hdr = self.tree.header()
        if hdr is None:
            return
        font = QtGui.QFont(NUMBER_FAMILY)
        font.setPixelSize(11)
        metrics = QtGui.QFontMetrics(font)
        for col, side in ((LEFT_COLUMN, "left"), (RIGHT_COLUMN, "right")):
            texts = [v for sid, v in self._values.items() if self._side(sid) == side]
            needed = VALUE_MIN_PX
            if texts:
                needed += max(metrics.horizontalAdvance(v) for v in texts) + 8
            current = hdr.sectionSize(col)
            hdr.resizeSection(col, max(needed, current) if keep_width else needed)

    def _side(self, sid: str) -> str:
        row = self._rows.get(sid)
        return "right" if row is not None and sid == row.right else "left"

    def value_text(self, sid: str) -> str:
        """What the swatch of `sid` shows beside it (its value at A, or "")."""
        return self._values.get(sid, "")
