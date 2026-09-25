"""
The Signals panel (R3.1, R6.2): legend, visibility, lanes and the cursor readout in one list.

Signals are grouped by the lane they're drawn in, in lane order. Each row has the signal's
color, a check box for visibility, and its value at the cursor (with Δ to the anchor while
paused). A lane's check box shows or hides all of its signals, and its header counts
how many are shown. The filter box narrows a long list (a PID frame has 34 signals).

Drag a signal onto another lane (or below the list, for a new lane), or right-click it to
move it. Moves and visibility last
across runs as view overrides (R5.3); the Configuration tab's Visible and Lane columns
change streams.json itself.
"""

from __future__ import annotations

import math

from PyQt6 import QtCore, QtGui, QtWidgets

from core.types import StreamConfig
from ui.charts.lanes import lane_layout
from ui.charts.series import Readout

NEW_LANE = "__new__"
DEFAULT_LANE_LABEL = "Main"
ROLE_SIGNAL = QtCore.Qt.ItemDataRole.UserRole
ROLE_LANE = QtCore.Qt.ItemDataRole.UserRole + 1
VALUE_COLUMN = 1


def _swatch(color: str) -> QtGui.QIcon:
    pixmap = QtGui.QPixmap(14, 14)
    pixmap.fill(QtGui.QColor(0, 0, 0, 0))
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    painter.setBrush(QtGui.QColor(color))
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QtCore.QRectF(1, 5, 12, 4), 2, 2)
    painter.end()
    return QtGui.QIcon(pixmap)


def format_value(value: float, delta: float | None = None) -> str:
    if not math.isfinite(value):
        return "n/a"
    text = f"{value:+.3f}"
    if delta is not None and math.isfinite(delta):
        text += f"  Δ {delta:+.3f}"
    return text


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


class SignalListPanel(QtWidgets.QWidget):
    """
    Signals grouped by lane. The owner applies what it emits (plot, persistence) and
    rebuilds the list for another stream with `rebuild_list`.
    """

    signal_visibility_changed = QtCore.pyqtSignal(str, bool)
    signal_lane_changed = QtCore.pyqtSignal(str, str, str)  # signal id, lane key, lane label

    def __init__(self) -> None:
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        header = QtWidgets.QHBoxLayout()
        self.count_lbl = QtWidgets.QLabel("")
        self.count_lbl.setStyleSheet("color: #9aa4b2;")
        header.addWidget(self.count_lbl)
        header.addStretch()
        self.cursor_lbl = QtWidgets.QLabel("")
        self.cursor_lbl.setStyleSheet("color: #9aa4b2;")
        header.addWidget(self.cursor_lbl)
        layout.addLayout(header)

        self.filter_edit = QtWidgets.QLineEdit()
        self.filter_edit.setPlaceholderText("Filter signals…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter_edit)

        self.tree = SignalTree()
        self.tree.dropped.connect(self.move_to_lane)
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(12)
        self.tree.setUniformRowHeights(True)
        self.tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.tree.itemChanged.connect(self._on_item_changed)
        hdr = self.tree.header()
        assert hdr is not None
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(VALUE_COLUMN, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.tree, 1)

        self._cfg: StreamConfig = {"signals": {}}
        self._lanes: list[tuple[str, str]] = []  # (key, label), in display order
        self._assignment: dict[str, str] = {}  # signal -> lane key
        self._visible: dict[str, bool] = {}
        self._items: dict[str, QtWidgets.QTreeWidgetItem] = {}
        self._lane_items: dict[str, QtWidgets.QTreeWidgetItem] = {}
        self._building = False

    # --- building ---

    def rebuild_list(self, cfg: StreamConfig) -> None:
        """Shows a stream's signals, grouped by its lanes."""
        self._cfg = cfg
        specs, assignment = lane_layout(cfg)
        self._lanes = [(s.key, s.label or DEFAULT_LANE_LABEL) for s in specs]
        self._assignment = dict(assignment)
        self._visible = {
            sid: bool(sig.get("visible", True)) for sid, sig in cfg.get("signals", {}).items()
        }
        self.cursor_lbl.setText("")
        self._rebuild_tree()

    def _rebuild_tree(self) -> None:
        self._building = True
        expanded = {key: item.isExpanded() for key, item in self._lane_items.items()}
        self.tree.clear()
        self._items.clear()
        self._lane_items.clear()
        signals = self._cfg.get("signals", {})
        for key, label in self._lanes:
            lane_item = QtWidgets.QTreeWidgetItem([label, ""])
            lane_item.setData(0, ROLE_LANE, key)
            lane_item.setFlags(
                QtCore.Qt.ItemFlag.ItemIsEnabled
                | QtCore.Qt.ItemFlag.ItemIsUserCheckable
                | QtCore.Qt.ItemFlag.ItemIsAutoTristate
                | QtCore.Qt.ItemFlag.ItemIsDropEnabled
            )
            font = lane_item.font(0)
            font.setBold(True)
            lane_item.setFont(0, font)
            self.tree.addTopLevelItem(lane_item)
            self._lane_items[key] = lane_item
            for sid, sig in signals.items():
                if self._assignment.get(sid) != key:
                    continue
                item = QtWidgets.QTreeWidgetItem([sig.get("label", sid), ""])
                item.setData(0, ROLE_SIGNAL, sid)
                item.setIcon(0, _swatch(sig.get("color", "#FFFFFF")))
                item.setToolTip(
                    0,
                    f"{sig.get('label', sid)} ({sig.get('field', sid)}): drag to another lane",
                )
                item.setFlags(
                    QtCore.Qt.ItemFlag.ItemIsEnabled
                    | QtCore.Qt.ItemFlag.ItemIsUserCheckable
                    | QtCore.Qt.ItemFlag.ItemIsSelectable
                    | QtCore.Qt.ItemFlag.ItemIsDragEnabled
                    | QtCore.Qt.ItemFlag.ItemIsDropEnabled  # a drop on a signal: its lane
                )
                item.setCheckState(
                    0,
                    QtCore.Qt.CheckState.Checked
                    if self._visible.get(sid, True)
                    else QtCore.Qt.CheckState.Unchecked,
                )
                item.setTextAlignment(
                    VALUE_COLUMN,
                    QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter,
                )
                item.setFont(VALUE_COLUMN, QtGui.QFont("monospace"))
                lane_item.addChild(item)
                self._items[sid] = item
            lane_item.setExpanded(expanded.get(key, True))
        self._building = False
        self._update_counts()
        self._apply_filter(self.filter_edit.text())

    def _update_counts(self) -> None:
        for key, lane_item in self._lane_items.items():
            members = [s for s, lane in self._assignment.items() if lane == key]
            shown = sum(self._visible.get(s, True) for s in members)
            lane_item.setText(VALUE_COLUMN, f"{shown}/{len(members)}")
        total = len(self._visible)
        self.count_lbl.setText(f"{sum(self._visible.values())} of {total} shown")

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for lane_item in self._lane_items.values():
            any_shown = False
            for i in range(lane_item.childCount()):
                child = lane_item.child(i)
                if child is None:
                    continue
                sid = child.data(0, ROLE_SIGNAL)
                match = not needle or needle in child.text(0).lower() or needle in str(sid)
                child.setHidden(not match)
                any_shown |= match
            lane_item.setHidden(not any_shown)
            if needle and any_shown:
                lane_item.setExpanded(True)

    # --- visibility ---

    def _on_item_changed(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        if self._building or column != 0:
            return
        sid = item.data(0, ROLE_SIGNAL)
        if not isinstance(sid, str):
            return  # a lane's box: Qt updates its signals, which land here one by one
        checked = item.checkState(0) == QtCore.Qt.CheckState.Checked
        if self._visible.get(sid) == checked:
            return
        self._visible[sid] = checked
        self._update_counts()
        self.signal_visibility_changed.emit(sid, checked)

    def is_visible(self, sid: str) -> bool:
        return self._visible.get(sid, False)

    def set_visible(self, sid: str, visible: bool) -> None:
        """Checks or unchecks a signal as the user would (emits the change)."""
        item = self._items.get(sid)
        if item is not None:
            item.setCheckState(
                0, QtCore.Qt.CheckState.Checked if visible else QtCore.Qt.CheckState.Unchecked
            )

    def set_lane_visible(self, lane: str, visible: bool) -> None:
        """Checks or unchecks every signal of a lane, like its check box."""
        item = self._lane_items.get(lane)
        if item is not None:
            item.setCheckState(
                0, QtCore.Qt.CheckState.Checked if visible else QtCore.Qt.CheckState.Unchecked
            )

    # --- lanes ---

    def lanes(self) -> list[tuple[str, str]]:
        return list(self._lanes)

    def lane_of(self, sid: str) -> str | None:
        return self._assignment.get(sid)

    def move_to_lane(self, sid: str, lane: str) -> None:
        """Moves a signal to a lane; `NEW_LANE` creates one ("Lane N")."""
        if sid not in self._assignment:
            return
        if lane == NEW_LANE:
            n = len(self._lanes) + 1
            taken = {k for k, _ in self._lanes}
            while f"Lane {n}" in taken:
                n += 1
            lane = f"Lane {n}"
            self._lanes.append((lane, lane))
        if self._assignment[sid] == lane:
            return
        self._assignment[sid] = lane
        label = next((lbl for k, lbl in self._lanes if k == lane), lane)
        # A lane left without signals disappears from the list, as from the plot.
        used = set(self._assignment.values())
        self._lanes = [(k, lbl) for k, lbl in self._lanes if k in used]
        self._rebuild_tree()
        self.signal_lane_changed.emit(sid, lane, label)

    def _on_context_menu(self, pos: QtCore.QPoint) -> None:
        item = self.tree.itemAt(pos)
        sid = item.data(0, ROLE_SIGNAL) if item is not None else None
        if not isinstance(sid, str):
            return
        menu = QtWidgets.QMenu(self)
        move = menu.addMenu("Move to lane")
        assert move is not None
        for key, label in self._lanes:
            act = move.addAction(label)
            assert act is not None
            act.setEnabled(key != self._assignment.get(sid))
            act.triggered.connect(lambda _=False, k=key: self.move_to_lane(sid, k))
        move.addSeparator()
        new = move.addAction("New lane")
        assert new is not None
        new.triggered.connect(lambda: self.move_to_lane(sid, NEW_LANE))
        viewport = self.tree.viewport()
        assert viewport is not None
        menu.exec(viewport.mapToGlobal(pos))

    # --- readout ---

    def show_readout(self, readout: Readout | None) -> None:
        """Each signal's value at the cursor, next to its name (R6.2)."""
        if readout is None:
            self.cursor_lbl.setText("")
            for item in self._items.values():
                item.setText(VALUE_COLUMN, "")
            return
        header = f"@ {readout.t:.3f} s"
        if readout.dt is not None:
            header += f"  Δt {readout.dt:+.3f}"
        self.cursor_lbl.setText(header)
        for sid, item in self._items.items():
            if not self._visible.get(sid, True) or sid not in readout.values:
                item.setText(VALUE_COLUMN, "")
                continue
            item.setText(VALUE_COLUMN, format_value(readout.values[sid], readout.deltas.get(sid)))

    def value_text(self, sid: str) -> str:
        item = self._items.get(sid)
        return item.text(VALUE_COLUMN) if item is not None else ""
