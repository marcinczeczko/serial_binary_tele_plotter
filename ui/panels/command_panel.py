"""
A control panel generated from a `panels` entry in streams.json (R5.2, R6.3, R6.4).

One row per parameter and one column per `columns` entry: a spin box for `float` and
`int` parameters, a check box for `bool`. A button placed in a column sits under it and
sends its command with that column's values; a button without a column spans the panel
(its command's fields name their columns).

What the panel adds on top of that:
- **Edited vs sent.** After a send, every value that differs from what was last sent is
  highlighted, and counted next to Revert. Before the first send, what the board holds is
  unknown, so nothing is marked.
- **Linked rows.** With two or more columns, a linked row keeps its columns equal: editing
  one edits the others. Rows whose values start equal start linked. The `L=R` box in the
  grid's corner links or unlinks every row; a single row is linked from its label's
  right-click menu, and an unlinked row's label shows `≠` (R9.5).
- **Live mode.** Each change sends its column's button after `LIVE_DEBOUNCE_MS` without
  further changes, and at most every `LIVE_MIN_INTERVAL_S`: tuning by dragging a spin box.
  It's off by default because it sends to real hardware as you type.
- **Presets.** Named value sets (kept by the owner, per config file), in the `Presets ▾`
  menu (load, Save as…, Delete); choosing one loads its values, which then count as edited
  until sent.
- **Ctrl+Enter** presses the panel's main button (the first one spanning the panel), and
  **Esc** reverts the values edited since the last send.
- **Scrubbing.** Dragging a number parameter's label left or right changes the row's values
  by one `step` per `SCRUB_PX_PER_STEP` pixels (Shift ×10, Alt ×0.1), like a knob. A linked
  row stays equal; an unlinked row moves every column by the same amount. It goes through
  the same path as typing, so edited marks and Live mode apply.

Looks (R9.5, ADR-0012): a Manual | Live segment, square black inputs without spin arrows
in B612 Mono, `Send` under each column (the button's label is its tooltip), and `Revert N`
in amber only while something is edited.

The panel only collects values: the main window resolves and encodes the command
(`core.protocol.commands`), hands the packet to the engine and calls `mark_sent`.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from PyQt6 import QtCore, QtGui, QtWidgets

from core.config.controls import ButtonDef, PanelDef, ParamDef
from styles import AMBER, TEXT_DIM, mono_font
from ui.common.numbers import ScopeDoubleSpinBox

ParamWidget = QtWidgets.QDoubleSpinBox | QtWidgets.QSpinBox | QtWidgets.QCheckBox

SCRUB_PX_PER_STEP = 4
SCRUB_FAST = 10.0  # with Shift
SCRUB_FINE = 0.1  # with Alt
LIVE_DEBOUNCE_MS = 150
LIVE_MIN_INTERVAL_S = 0.1
EDITED_STYLE = "border: 1px solid #FFB000; color: #FFB000;"  # amber = edited, not sent
LIVE_STYLE = "QToolButton { background: #FFB000; color: #000; border-color: #FFB000; }"
REVERT_STYLE = (
    f"QPushButton {{ color: {AMBER}; border-color: {AMBER}; background: transparent;"
    " font-weight: bold; }"
)
PANEL_STYLE = (
    "QCheckBox::indicator { width: 13px; height: 13px; }"
    f" QLabel#column_label {{ color: {TEXT_DIM}; }}"
)
UNLINKED_MARK = " ≠"


@dataclass(frozen=True)
class SendRequest:
    """A button press: what to send, and every parameter's value at that moment."""

    panel: str
    button: ButtonDef
    column: str | None
    params: dict[str, dict[str, float]]
    live: bool = False  # sent by Live mode, not a button press


def scrub_factor(modifiers: QtCore.Qt.KeyboardModifier) -> float:
    if modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier:
        return SCRUB_FAST
    if modifiers & QtCore.Qt.KeyboardModifier.AltModifier:
        return SCRUB_FINE
    return 1.0


class ScrubLabel(QtWidgets.QLabel):
    """
    A parameter's label that changes its value when dragged horizontally. It reports steps
    moved since the press (fractional with Alt); the panel applies them from the values the
    drag started at, so a drag back to where it started restores them exactly.
    """

    scrub_started = QtCore.pyqtSignal()
    scrubbed = QtCore.pyqtSignal(float)  # steps since the press

    def __init__(self, text: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(QtCore.Qt.CursorShape.SizeHorCursor)
        self.setToolTip("Drag left or right to change · Shift ×10 · Alt ×0.1")
        self._press_x: float | None = None

    def mousePressEvent(self, event: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if event is not None and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._press_x = event.position().x()
            self.scrub_started.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if event is None or self._press_x is None:
            super().mouseMoveEvent(event)
            return
        pixels = event.position().x() - self._press_x
        steps = pixels / SCRUB_PX_PER_STEP * scrub_factor(event.modifiers())
        self.scrubbed.emit(round(steps, 6))
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        self._press_x = None
        super().mouseReleaseEvent(event)


class CommandPanel(QtWidgets.QWidget):
    send_requested = QtCore.pyqtSignal(object)  # SendRequest
    values_changed = QtCore.pyqtSignal()
    preset_saved = QtCore.pyqtSignal(str, object)  # name, {column: {param: value}}
    preset_deleted = QtCore.pyqtSignal(str)
    live_changed = QtCore.pyqtSignal(bool)

    def __init__(self, panel: PanelDef, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.panel = panel
        self.inputs: dict[str, dict[str, ParamWidget]] = {c: {} for c in panel.column_keys}
        self.buttons: list[QtWidgets.QPushButton] = []
        self.links: dict[str, QtGui.QAction] = {}  # a row's link, from its label's menu
        self._sent: dict[str, dict[str, float]] = {}
        self._syncing = False
        self._presets: dict[str, dict[str, dict[str, float]]] = {}
        self._live_timers: dict[str, QtCore.QTimer] = {}
        self._last_live: dict[str, float] = {}
        self._button_widgets: dict[int, QtWidgets.QPushButton] = {}
        self._by_label: dict[str, QtWidgets.QPushButton] = {}  # by the config's label
        self.labels: dict[str, QtWidgets.QLabel] = {}
        self._scrub_start: dict[str, dict[str, float]] = {}  # row -> column -> value

        self.setStyleSheet(PANEL_STYLE)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        # --- Manual | Live, and Presets ▾ ---
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(0)
        self.manual_btn = QtWidgets.QToolButton()
        self.manual_btn.setText("Manual")
        self.live_btn = QtWidgets.QToolButton()
        self.live_btn.setText("Live")
        self.live_btn.setToolTip(
            f"Send a column {LIVE_DEBOUNCE_MS} ms after its values stop changing "
            f"(at most every {LIVE_MIN_INTERVAL_S * 1000:.0f} ms). Sends to the device as "
            "you edit."
        )
        mode = QtWidgets.QButtonGroup(self)
        for btn in (self.manual_btn, self.live_btn):
            btn.setCheckable(True)
            mode.addButton(btn)
            top.addWidget(btn)
        self.manual_btn.setChecked(True)
        self.live_btn.toggled.connect(self._on_live_toggled)
        top.addStretch()
        self.presets_btn = QtWidgets.QToolButton()
        self.presets_btn.setText("Presets ▾")
        self.presets_btn.setToolTip("Load a saved set of values (then send it), save or delete")
        self.presets_btn.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.presets_menu = QtWidgets.QMenu(self.presets_btn)
        self.presets_btn.setMenu(self.presets_menu)
        top.addWidget(self.presets_btn)
        outer.addLayout(top)

        # --- parameter grid: the L=R box in the corner, then one row per parameter ---
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        linkable = len(panel.columns) >= 2
        self.link_all = QtWidgets.QToolButton()
        self.link_all.setCheckable(True)
        self.link_all.setText(_link_text(panel.columns))
        self.link_all.setToolTip(
            "Link every row: the columns stay equal. A single row: right-click its label."
        )
        self.link_all.setVisible(linkable)
        self.link_all.toggled.connect(self._on_link_all)
        row = 0
        if panel.columns:
            if linkable:
                grid.addWidget(self.link_all, row, 0)
            for i, col in enumerate(panel.columns):
                heading = QtWidgets.QLabel(col)
                heading.setObjectName("column_label")
                grid.addWidget(heading, row, 1 + i)
            row += 1
        for param in panel.parameters:
            label: QtWidgets.QLabel
            if param.kind == "bool":
                label = QtWidgets.QLabel(param.label)
            else:
                scrub = ScrubLabel(param.label)
                scrub.scrub_started.connect(lambda p=param.key: self._begin_scrub(p))
                scrub.scrubbed.connect(lambda steps, p=param: self.scrub(p.key, steps))
                label = scrub
            self.labels[param.key] = label
            grid.addWidget(label, row, 0)
            if linkable:
                link = QtGui.QAction(f"Link {param.label} across columns", self)
                link.setCheckable(True)
                self.links[param.key] = link
                label.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
                label.customContextMenuRequested.connect(
                    lambda pos, lbl=label, a=link: self._row_menu(lbl, a, pos)
                )
            for i, col in enumerate(panel.column_keys):
                widget = self._input(param, col)
                self.inputs[col][param.key] = widget
                grid.addWidget(widget, row, 1 + i)
            row += 1

        # Column buttons under their column, stacked; the others span the panel.
        per_column: dict[str, int] = {}
        spanning: list[ButtonDef] = []
        placed = [b.column for b in panel.buttons if b.column is not None]
        for button in panel.buttons:
            if button.column is None or not panel.columns:
                spanning.append(button)
                continue
            i = panel.columns.index(button.column)
            offset = per_column.get(button.column, 0)
            per_column[button.column] = offset + 1
            short = placed.count(button.column) == 1  # alone under its column: "Send"
            grid.addWidget(self._button(button, short), row + offset, 1 + i)
        row += max(per_column.values(), default=0)
        span = len(panel.column_keys) + 1
        for button in spanning:
            grid.addWidget(self._button(button), row, 0, 1, span)
            row += 1
        grid.setColumnStretch(0, 0)
        for i in range(len(panel.column_keys)):
            grid.setColumnStretch(1 + i, 1)
        outer.addLayout(grid)

        # --- edited vs sent: Revert N, only while something is edited ---
        status = QtWidgets.QHBoxLayout()
        self.revert_btn = QtWidgets.QPushButton("Revert")
        self.revert_btn.setToolTip("Back to the last sent values (Esc)")
        self.revert_btn.setStyleSheet(REVERT_STYLE)
        self.revert_btn.setVisible(False)
        self.revert_btn.clicked.connect(self.revert)
        status.addWidget(self.revert_btn)
        status.addStretch()
        outer.addLayout(status)
        outer.addStretch()
        self._current_preset: str | None = None
        self.set_presets({})

        shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self)
        shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self.press_default)
        revert = QtGui.QShortcut(QtGui.QKeySequence("Escape"), self)
        revert.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        revert.activated.connect(self.revert)

        self.reset_links()
        for link in self.links.values():
            link.toggled.connect(lambda _=False: self._sync_link_all())

    # --- widgets ---

    def _input(self, param: ParamDef, col: str) -> ParamWidget:
        widget: ParamWidget
        if param.kind == "bool":
            widget = QtWidgets.QCheckBox()
            widget.setChecked(bool(param.default))
            widget.toggled.connect(lambda _=False, c=col, p=param.key: self._on_edit(c, p))
            return widget
        if param.kind == "int":
            widget = QtWidgets.QSpinBox()
            widget.setRange(int(param.minimum), int(param.maximum))
            widget.setSingleStep(max(int(param.step), 1))
            widget.setValue(int(param.default))
        else:
            widget = ScopeDoubleSpinBox()
            widget.setRange(param.minimum, param.maximum)
            widget.setDecimals(param.decimals)
            widget.setSingleStep(param.step)
            widget.setValue(param.default)
        widget.setKeyboardTracking(False)
        widget.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        widget.setFont(mono_font())
        widget.setAccessibleName(f"{param.label} {col}".strip())
        widget.valueChanged.connect(lambda _=0, c=col, p=param.key: self._on_edit(c, p))
        return widget

    def _button(self, button: ButtonDef, short: bool = False) -> QtWidgets.QPushButton:
        btn = QtWidgets.QPushButton("Send" if short else button.label)
        btn.setToolTip(button.label)
        self._by_label[button.label] = btn
        btn.clicked.connect(lambda _checked=False, b=button: self._send(b))
        self.buttons.append(btn)
        self._button_widgets[id(button)] = btn
        return btn

    # --- values ---

    def values(self) -> dict[str, dict[str, float]]:
        """{column: {parameter: value}}; check boxes are 1.0 or 0.0."""
        return {
            col: {key: _value(widget) for key, widget in widgets.items()}
            for col, widgets in self.inputs.items()
        }

    def _value(self, col: str, key: str) -> float:
        return _value(self.inputs[col][key])

    def set_values(self, values: dict[str, dict[str, float]], notify: bool = False) -> None:
        """Restores values (e.g. from the last session or a preset); unknown ones are ignored."""
        self._syncing = True
        try:
            for col, params in values.items():
                for key, value in params.items():
                    widget = self.inputs.get(col, {}).get(key)
                    if widget is not None:
                        _set(widget, value)
        finally:
            self._syncing = False
        self._refresh_edited()
        if notify:
            self.values_changed.emit()
            if self.live:
                for col in values:
                    self._schedule_live(col)

    def _on_edit(self, col: str, key: str) -> None:
        if self._syncing:
            return
        link = self.links.get(key)
        cols = [col]
        if link is not None and link.isChecked():
            value = self._value(col, key)
            self._syncing = True
            try:
                for other in self.panel.column_keys:
                    if other != col:
                        _set(self.inputs[other][key], value)
                        cols.append(other)
            finally:
                self._syncing = False
        self._refresh_edited()
        self.values_changed.emit()
        if self.live:
            for c in cols:
                self._schedule_live(c)

    # --- scrubbing ---

    def _begin_scrub(self, key: str) -> None:
        self._scrub_start[key] = {col: self._value(col, key) for col in self.panel.column_keys}

    def scrub(self, key: str, steps: float) -> None:
        """
        Moves a row `steps` parameter steps away from where the drag started (see
        `ScrubLabel`). Values are clamped by their spin boxes' ranges.
        """
        param = next((p for p in self.panel.parameters if p.key == key), None)
        if param is None or param.kind == "bool":
            return
        if key not in self._scrub_start:
            self._begin_scrub(key)
        link = self.links.get(key)
        linked = link is not None and link.isChecked()
        cols = self.panel.column_keys[:1] if linked else self.panel.column_keys
        for col in cols:
            start = self._scrub_start[key][col]
            widget = self.inputs[col][key]
            target = start + steps * param.step
            if isinstance(widget, QtWidgets.QSpinBox):
                widget.setValue(round(target))
            elif isinstance(widget, QtWidgets.QDoubleSpinBox):
                widget.setValue(target)

    # --- linking ---

    def reset_links(self) -> None:
        """Links exactly the rows whose columns hold equal values (e.g. after a restore)."""
        for key, link in self.links.items():
            values = {self._value(col, key) for col in self.panel.column_keys}
            link.blockSignals(True)
            link.setChecked(len(values) == 1)
            link.blockSignals(False)
        self._sync_link_all()

    def _on_link_all(self, checked: bool) -> None:
        for key, link in self.links.items():
            link.blockSignals(True)
            link.setChecked(checked)
            link.blockSignals(False)
            if checked:  # linking makes the columns equal: the first column wins
                first = self.panel.column_keys[0]
                self._on_edit(first, key)
        self._sync_link_all()

    def _sync_link_all(self) -> None:
        if not self.links:
            return
        self.link_all.blockSignals(True)
        self.link_all.setChecked(all(link.isChecked() for link in self.links.values()))
        self.link_all.blockSignals(False)
        for key, link in self.links.items():
            param = next(p for p in self.panel.parameters if p.key == key)
            self.labels[key].setText(param.label + ("" if link.isChecked() else UNLINKED_MARK))

    def _row_menu(self, label: QtWidgets.QLabel, link: QtGui.QAction, pos: QtCore.QPoint) -> None:
        menu = QtWidgets.QMenu(self)
        menu.addAction(link)
        menu.exec(label.mapToGlobal(pos))

    # --- edited vs sent ---

    def mark_sent(self, sent: dict[str, dict[str, float]]) -> None:
        """Records what a send carried ({column: {parameter: value}})."""
        for col, params in sent.items():
            self._sent.setdefault(col, {}).update(params)
        self._refresh_edited()

    def last_sent(self, col: str, key: str) -> float | None:
        return self._sent.get(col, {}).get(key)

    def edited(self) -> list[tuple[str, str]]:
        """(column, parameter) whose value differs from what was last sent."""
        out: list[tuple[str, str]] = []
        for col, params in self._sent.items():
            for key, sent in params.items():
                if key in self.inputs.get(col, {}) and abs(self._value(col, key) - sent) > 1e-12:
                    out.append((col, key))
        return out

    def revert(self) -> None:
        """Puts every edited value back to what was last sent (also Esc)."""
        self.set_values({col: dict(params) for col, params in self._sent.items()}, notify=False)
        self.values_changed.emit()

    def _refresh_edited(self) -> None:
        edited = set(self.edited())
        for col, widgets in self.inputs.items():
            for key, widget in widgets.items():
                is_edited = (col, key) in edited
                widget.setStyleSheet(EDITED_STYLE if is_edited else "")
                sent = self.last_sent(col, key)
                widget.setToolTip(f"Last sent: {sent:g}" if sent is not None else "Not sent yet")
        self.revert_btn.setText(f"Revert {len(edited)}")
        self.revert_btn.setVisible(bool(edited))

    # --- sending ---

    def _send(self, button: ButtonDef, live: bool = False) -> None:
        request = SendRequest(
            self.panel.key, button, self.panel.button_column(button), self.values(), live
        )
        self.send_requested.emit(request)

    def press_default(self) -> None:
        """Ctrl+Enter: the first button spanning the panel, else the first button."""
        spanning = [b for b in self.panel.buttons if b.column is None]
        buttons = spanning or list(self.panel.buttons)
        if buttons:
            self._send(buttons[0])

    def button_for(self, label: str) -> QtWidgets.QPushButton | None:
        """The button of the config's `label` (shown as `Send` under its column)."""
        return self._by_label.get(label)

    # --- live mode ---

    @property
    def live(self) -> bool:
        return self.live_btn.isChecked()

    def set_live(self, live: bool) -> None:
        (self.live_btn if live else self.manual_btn).setChecked(True)

    def _on_live_toggled(self, live: bool) -> None:
        self.live_btn.setStyleSheet(LIVE_STYLE if live else "")
        if not live:
            for timer in self._live_timers.values():
                timer.stop()
        self.live_changed.emit(live)

    def live_button(self, col: str) -> ButtonDef | None:
        """The button Live mode presses for a column: the first one placed in it."""
        if self.panel.columns:
            return next((b for b in self.panel.buttons if b.column == col), None)
        return next(iter(self.panel.buttons), None)

    def _schedule_live(self, col: str) -> None:
        if self.live_button(col) is None:
            return
        timer = self._live_timers.get(col)
        if timer is None:
            timer = QtCore.QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda c=col: self._fire_live(c))
            self._live_timers[col] = timer
        timer.start(LIVE_DEBOUNCE_MS)

    def _fire_live(self, col: str, clock: Callable[[], float] = time.monotonic) -> None:
        button = self.live_button(col)
        if button is None or not self.live:
            return
        wait = self._last_live.get(col, -1e9) + LIVE_MIN_INTERVAL_S - clock()
        if wait > 0:
            self._live_timers[col].start(int(wait * 1000) + 1)
            return
        self._last_live[col] = clock()
        self._send(button, live=True)

    # --- presets ---

    def set_presets(self, presets: dict[str, dict[str, dict[str, float]]]) -> None:
        self._presets = dict(presets)
        if self._current_preset not in self._presets:
            self._current_preset = None
        self._build_presets_menu()

    def _build_presets_menu(self) -> None:
        """Presets ▾: each preset (the loaded one checked), Save as…, Delete ▸."""
        menu = self.presets_menu
        menu.clear()
        for name in sorted(self._presets):
            act = menu.addAction(name)
            assert act is not None
            act.setCheckable(True)
            act.setChecked(name == self._current_preset)
            act.triggered.connect(lambda _=False, n=name: self.apply_preset(n))
        if self._presets:
            menu.addSeparator()
        save = menu.addAction("Save as…")
        assert save is not None
        save.triggered.connect(self._ask_preset_name)
        delete = menu.addMenu("Delete")
        assert delete is not None
        delete.setEnabled(bool(self._presets))
        for name in sorted(self._presets):
            act = delete.addAction(name)
            assert act is not None
            act.triggered.connect(lambda _=False, n=name: self.delete_preset(n))
        self.presets_btn.setText(
            f"{self._current_preset} ▾" if self._current_preset else "Presets ▾"
        )

    def preset_names(self) -> list[str]:
        return sorted(self._presets)

    def apply_preset(self, name: str) -> None:
        values = self._presets.get(name)
        if values is not None:
            self.set_values(values, notify=True)
            self._current_preset = name
            self._build_presets_menu()

    def save_preset(self, name: str) -> None:
        name = name.strip()
        if not name:
            return
        self._presets[name] = self.values()
        self._current_preset = name
        self._build_presets_menu()
        self.preset_saved.emit(name, self.values())

    def delete_preset(self, name: str) -> None:
        if self._presets.pop(name, None) is not None:
            if self._current_preset == name:
                self._current_preset = None
            self._build_presets_menu()
            self.preset_deleted.emit(name)

    def _ask_preset_name(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "Save preset", "Preset name:")
        if ok:
            self.save_preset(name)


def _link_text(columns: tuple[str, ...] | list[str]) -> str:
    """The corner box: `L=R` for Left/Right, the columns' initials in general."""
    return "=".join(c[:1].upper() for c in columns) if columns else ""


def _value(widget: ParamWidget) -> float:
    if isinstance(widget, QtWidgets.QCheckBox):
        return 1.0 if widget.isChecked() else 0.0
    return float(widget.value())


def _set(widget: ParamWidget, value: float) -> None:
    widget.blockSignals(True)
    if isinstance(widget, QtWidgets.QCheckBox):
        widget.setChecked(bool(value))
    elif isinstance(widget, QtWidgets.QSpinBox):
        widget.setValue(int(value))
    else:
        widget.setValue(float(value))
    widget.blockSignals(False)
