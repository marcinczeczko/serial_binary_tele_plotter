"""
Stream editor (R7.1): one stream, laid out like the scope.

- One row of stream settings: key, name, ID, byte order, X axis and time per tick, controls.
- The frame, as `FrameView` draws it: what the device sends, in wire order.
- The signals by lane, like the dashboard's Signals dock, with the fields that aren't
  plotted in their own group: how it's shown. Dragging a field or signal onto a lane plots
  it there; dragging a signal onto "Not plotted" removes it.
- A form for the selected field and its signal.

In a text profile (R8.4) a stream is a line pattern: Pattern replaces ID and byte order,
`LineView` replaces the frame view, and the form edits a value (its slot in the pattern).

Every edit is an operation on a `StreamDraft` (`core/config/draft.py`), so the editor
never rebuilds a stream from its widgets and can't lose what it doesn't show (C4).
"""

from __future__ import annotations

import re
from typing import Any

from PyQt6 import QtCore, QtGui, QtWidgets

from core.config import ENDIANNESS, MAX_PAYLOAD_BYTES
from core.config.draft import LINE_STYLES, StreamDraft, unique_name
from core.protocol.constants import STRUCT_TYPE_MAP
from core.protocol.text_line import LINE_FIELD, Pattern, PatternError, parse_pattern
from core.types import StreamConfig
from ui.charts.lanes import DEFAULT_LANE, lane_layout
from ui.common.color_button import ColorButton
from ui.config.frame_view import TIME_FIELD, FrameView
from ui.config.line_view import LineView
from ui.panels.signals import DEFAULT_LANE_LABEL, NEW_LANE, ROLE_LANE, ROLE_SIGNAL, SignalTree

ROLE_FIELD = QtCore.Qt.ItemDataRole.UserRole + 2
UNPLOTTED_LANE = "__unplotted__"
FIELD_MARK = "field:"  # ROLE_SIGNAL of a row for a field without a signal
LINE_NUMBER = "(line number)"  # the X axis of a text stream without a counter
# The value types the editor offers for text; a file's other types are shown as they are.
TEXT_TYPES = (("f32", "number"), ("u32", "integer"), ("i32", "signed integer"))
MONO_STYLE = "font-family: 'DejaVu Sans Mono', Menlo, monospace;"
PATTERN_ERROR_STYLE = "QLineEdit { border: 1px solid #ff6b6b; " + MONO_STYLE + " }"
MUTED = QtGui.QColor("#888888")
DIM = QtGui.QColor("#666666")


def format_seconds(value: Any) -> str:
    """5 ms, 1 µs, 0.5 s: the time per tick as people say it."""
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        return str(value)
    if value >= 1:
        return f"{value:g} s"
    if value >= 1e-3:
        return f"{value * 1e3:g} ms"
    if value >= 1e-6:
        return f"{value * 1e6:g} µs"
    return f"{value:g} s"


def parse_seconds(text: str) -> float | None:
    """Reads `format_seconds` back; a bare number is in seconds."""
    m = re.fullmatch(r"\s*([0-9.]+(?:[eE][-+]?\d+)?)\s*(s|ms|us|µs)?\s*", text)
    if m is None:
        return None
    try:
        number = float(m.group(1))
    except ValueError:
        return None
    divisor = {"ms": 1e3, "us": 1e6, "µs": 1e6}.get(m.group(2) or "s", 1.0)
    value = number / divisor
    return value if value > 0 else None


def _parse_number(text: str) -> int | float | None:
    text = text.strip()
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _line_icon(color: str, style: str) -> QtGui.QIcon:
    pixmap = QtGui.QPixmap(22, 12)
    pixmap.fill(QtGui.QColor(0, 0, 0, 0))
    painter = QtGui.QPainter(pixmap)
    pen = QtGui.QPen(QtGui.QColor(color), 3)
    pen.setStyle(
        {"dashed": QtCore.Qt.PenStyle.DashLine, "dotted": QtCore.Qt.PenStyle.DotLine}.get(
            style, QtCore.Qt.PenStyle.SolidLine
        )
    )
    painter.setPen(pen)
    painter.drawLine(1, 6, 21, 6)
    painter.end()
    return QtGui.QIcon(pixmap)


HEADER_STYLE = (
    "QHeaderView::section { background: #000; color: #888; border: none;"
    " border-bottom: 1px solid #333; padding: 2px 4px; }"
)


class HexSpinBox(QtWidgets.QSpinBox):
    """A byte, shown as the firmware writes it: 0x04."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRange(0, 255)
        self.setPrefix("0x")

    def textFromValue(self, v: int) -> str:  # noqa: N802
        return f"{v:02X}"

    def valueFromText(self, text: str | None) -> int:  # noqa: N802
        digits = (text or "").removeprefix(self.prefix()).strip()
        try:
            return int(digits, 16)
        except ValueError:
            return self.value()

    def validate(self, text: str | None, pos: int) -> tuple[QtGui.QValidator.State, str, int]:
        digits = (text or "").removeprefix(self.prefix()).strip()
        if digits == "":
            return QtGui.QValidator.State.Intermediate, text or "", pos
        try:
            ok = 0 <= int(digits, 16) <= 255
        except ValueError:
            ok = False
        state = QtGui.QValidator.State.Acceptable if ok else QtGui.QValidator.State.Invalid
        return state, text or "", pos


def field_colors(draft: StreamDraft) -> dict[str, str]:
    """Each field's color in the frame view: its first signal's, the counter's own."""
    colors: dict[str, str] = {}
    for sig in draft.signals.values():
        if isinstance(sig, dict) and isinstance(sig.get("field"), str):
            colors.setdefault(sig["field"], str(sig.get("color", "#ffffff")))
    time_field = draft.time_value("field")
    if isinstance(time_field, str):
        colors[time_field] = TIME_FIELD
    return colors


class PatternEdit(QtWidgets.QLineEdit):
    """The Pattern field: Esc drops what was typed (a pattern is applied on leaving)."""

    escaped = QtCore.pyqtSignal()

    def keyPressEvent(self, event: QtGui.QKeyEvent | None) -> None:  # noqa: N802
        if event is not None and event.key() == QtCore.Qt.Key.Key_Escape:
            self.escaped.emit()
            return
        super().keyPressEvent(event)


class StreamEditor(QtWidgets.QWidget):
    changed = QtCore.pyqtSignal()  # the draft changed
    key_rename_requested = QtCore.pyqtSignal(str)  # the tab renames (it knows the other keys)
    problem = QtCore.pyqtSignal(str)  # an edit that was refused, and why

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_stream_key: str | None = None
        self.draft = StreamDraft({"name": "", "frame": {"fields": []}})
        self._field: str | None = None  # selected field
        self._signal: str | None = None  # selected signal (of that field), if plotted
        self._loading = False
        self._flushing = False
        # Line edits the user typed in and hasn't left: flush() applies only these, so text
        # a redraw left behind is never taken for an edit.
        self._typed: set[QtWidgets.QLineEdit] = set()
        self._panel_keys: list[str] = []
        self._text = False  # the profile is text lines (R8.4)
        self._pattern_error: str | None = None  # the Pattern typed doesn't parse
        self._last_valid: Pattern | None = None  # what the line view shows meanwhile
        self._build()
        self.setEnabled(False)

    # --- construction ---

    def _build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(8, 6, 8, 0)
        row.setSpacing(6)
        self.key_edit = QtWidgets.QLineEdit()
        self.key_edit.setFixedWidth(110)
        self.key_edit.setToolTip("The stream's key in streams.json")
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setFixedWidth(170)
        self.id_spin = HexSpinBox()
        self.id_spin.setFixedWidth(64)
        self.endian_combo = QtWidgets.QComboBox()
        self.endian_combo.addItems(ENDIANNESS)
        self.pattern_edit = PatternEdit()
        self.pattern_edit.setFixedWidth(300)
        self.pattern_edit.setStyleSheet("QLineEdit { " + MONO_STYLE + " }")
        self.pattern_edit.setToolTip(
            "The line the board prints: fixed text and a {name} per value, e.g. "
            "IMU,{ms},{ax},{ay}. Applied when you leave the field; Esc drops the edit."
        )
        self.time_field_combo = QtWidgets.QComboBox()
        self.time_field_combo.setToolTip("The field that drives the X axis")
        self.time_scale_edit = QtWidgets.QLineEdit()
        self.time_scale_edit.setFixedWidth(64)
        self.time_scale_edit.setToolTip(
            "Time per tick of the X-axis field: the MCU loop period for a loop counter "
            "(5 ms), or 1 µs for a microsecond timestamp"
        )
        self.time_step_edit = QtWidgets.QLineEdit()
        self.time_step_edit.setFixedWidth(50)
        self.time_step_edit.setToolTip(
            "How much the X-axis field goes up per frame: 1 for a loop counter. A larger "
            "jump is drawn as a gap (lost frames)."
        )
        self.time_unit_lbl = QtWidgets.QLabel("per line")
        self.time_unit_lbl.setStyleSheet("color: #888;")
        self.panel_combo = QtWidgets.QComboBox()  # the stream's `controls` panel (R5.2)
        self.set_panel_choices([])
        self._row_labels: dict[QtWidgets.QWidget, QtWidgets.QLabel] = {}
        for label, widget in (
            ("Key:", self.key_edit),
            ("Name:", self.name_edit),
            ("ID:", self.id_spin),
            ("Byte order:", self.endian_combo),
            ("Pattern:", self.pattern_edit),
            ("X axis:", self.time_field_combo),
            ("×", self.time_scale_edit),
            ("Step:", self.time_step_edit),
            ("Controls:", self.panel_combo),
        ):
            lbl = QtWidgets.QLabel(label)
            if label not in ("Key:", "×"):
                lbl.setContentsMargins(8, 0, 0, 0)
            row.addWidget(lbl)
            row.addWidget(widget)
            self._row_labels[widget] = lbl
            if widget is self.time_scale_edit:
                row.addWidget(self.time_unit_lbl)
        row.addStretch()
        layout.addLayout(row)

        frame_box = QtWidgets.QVBoxLayout()
        frame_box.setContentsMargins(8, 0, 8, 0)
        frame_box.setSpacing(3)
        head = QtWidgets.QHBoxLayout()
        self.frame_title = QtWidgets.QLabel("Frame")
        self.frame_title.setStyleSheet("color: #aaa; font-weight: bold;")
        self.size_lbl = QtWidgets.QLabel("")
        head.addWidget(self.frame_title)
        head.addStretch()
        head.addWidget(self.size_lbl)
        frame_box.addLayout(head)
        self.frame_view = FrameView()
        frame_box.addWidget(self.frame_view)
        self.line_view = LineView()
        frame_box.addWidget(self.line_view)
        layout.addLayout(frame_box)

        split = QtWidgets.QSplitter()
        split.setHandleWidth(1)
        split.setStyleSheet("QSplitter::handle { background-color: #333; }")
        self.tree = SignalTree()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Signal", "Field", "Type", "Byte"])
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(14)
        self.tree.setUniformRowHeights(True)
        self.tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.setStyleSheet(
            "QTreeWidget { border: none; }"
            " QTreeWidget::item:selected { background: #1f3b5c; color: white; }" + HEADER_STYLE
        )
        header = self.tree.header()
        assert header is not None
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for col, width in ((1, 200), (2, 50), (3, 50)):
            header.setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeMode.Fixed)
            header.resizeSection(col, width)
        split.addWidget(self.tree)
        split.addWidget(self._build_form())
        split.setStretchFactor(0, 1)
        split.setSizes([900, 340])
        layout.addWidget(split, 1)

        self.key_edit.editingFinished.connect(self._on_key_edited)
        self.name_edit.editingFinished.connect(self._on_name_edited)
        self.id_spin.valueChanged.connect(self._on_id_changed)
        self.endian_combo.activated.connect(self._on_endianness)
        self.time_field_combo.activated.connect(self._on_time_field)
        self.pattern_edit.editingFinished.connect(self._on_pattern)
        self.pattern_edit.escaped.connect(self._revert_pattern)
        self.time_scale_edit.editingFinished.connect(self._on_time_scale)
        self.time_step_edit.editingFinished.connect(self._on_time_step)
        self.panel_combo.activated.connect(self._on_panel)
        for edit in (
            self.name_edit,
            self.time_scale_edit,
            self.time_step_edit,
            self.label_edit,
            self.field_name_edit,
            self.pattern_edit,
        ):
            edit.textEdited.connect(lambda _text, e=edit: self._typed.add(e))
            edit.editingFinished.connect(lambda e=edit: self._typed.discard(e))
        self.frame_view.field_clicked.connect(self._on_frame_clicked)
        self.frame_view.menu_requested.connect(self._show_field_menu)
        self.line_view.field_clicked.connect(self._on_frame_clicked)
        self.line_view.menu_requested.connect(self._show_field_menu)
        self.tree.currentItemChanged.connect(self._on_tree_current)
        self.tree.itemChanged.connect(self._on_tree_item_changed)
        self.tree.dropped.connect(self._on_dropped)
        self.tree.customContextMenuRequested.connect(self._on_tree_menu)

    def _build_form(self) -> QtWidgets.QWidget:
        box = QtWidgets.QWidget()
        box.setMinimumWidth(300)
        outer = QtWidgets.QVBoxLayout(box)
        outer.setContentsMargins(12, 6, 10, 8)
        self.form_title = QtWidgets.QLabel("Field")
        self.form_title.setStyleSheet("color: #aaa; font-weight: bold;")
        outer.addWidget(self.form_title)

        form = QtWidgets.QFormLayout()
        self.field_name_edit = QtWidgets.QLineEdit()
        self.field_type_combo = QtWidgets.QComboBox()
        self._fill_type_combo()
        self.field_byte_lbl = QtWidgets.QLabel("")
        self.field_byte_title = QtWidgets.QLabel("Byte:")
        form.addRow("Name:", self.field_name_edit)
        form.addRow("Type:", self.field_type_combo)
        form.addRow(self.field_byte_title, self.field_byte_lbl)
        outer.addLayout(form)

        self.signal_box = QtWidgets.QWidget()
        sform = QtWidgets.QFormLayout(self.signal_box)
        sform.setContentsMargins(0, 6, 0, 0)
        self.label_edit = QtWidgets.QLineEdit()
        self.lane_combo = QtWidgets.QComboBox()
        self.lane_combo.setEditable(True)
        self.lane_combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.lane_combo.setToolTip("Pick a lane, or type a new lane's name")
        self.color_btn = ColorButton()
        self.style_combo = QtWidgets.QComboBox()
        self.style_combo.addItems(LINE_STYLES)
        self.width_spin = QtWidgets.QSpinBox()
        self.width_spin.setRange(1, 5)
        self.width_spin.setSuffix(" px")
        line = QtWidgets.QHBoxLayout()
        line.addWidget(self.style_combo, 1)
        line.addWidget(self.width_spin)
        self.shown_chk = QtWidgets.QCheckBox("when the stream opens")
        sform.addRow("Label:", self.label_edit)
        sform.addRow("Lane:", self.lane_combo)
        sform.addRow("Color:", self.color_btn)
        sform.addRow("Line:", line)
        sform.addRow("Shown:", self.shown_chk)
        outer.addWidget(self.signal_box)

        self.not_plotted_lbl = QtWidgets.QLabel("Not plotted.")
        self.not_plotted_lbl.setStyleSheet("color: #888;")
        outer.addWidget(self.not_plotted_lbl)
        outer.addStretch()

        self.plot_btn = QtWidgets.QPushButton("Plot this field")
        buttons = QtWidgets.QHBoxLayout()
        self.add_field_btn = QtWidgets.QPushButton("Add field after")
        self.remove_field_btn = QtWidgets.QPushButton("Remove field")
        buttons.addWidget(self.add_field_btn)
        buttons.addWidget(self.remove_field_btn)
        outer.addWidget(self.plot_btn)
        outer.addLayout(buttons)

        self.field_name_edit.editingFinished.connect(self._on_field_name)
        self.field_type_combo.activated.connect(self._on_field_type)
        self.label_edit.editingFinished.connect(self._on_label)
        self.lane_combo.activated.connect(self._on_lane_picked)
        lane_edit = self.lane_combo.lineEdit()
        assert lane_edit is not None
        lane_edit.editingFinished.connect(self._on_lane_typed)
        self.color_btn.colorChanged.connect(lambda c: self._set_signal("color", c))
        self.style_combo.activated.connect(
            lambda _i: self._set_signal("style", self.style_combo.currentText())
        )
        self.width_spin.valueChanged.connect(lambda v: self._set_signal("width", v))
        self.shown_chk.toggled.connect(lambda on: self._set_signal("visible", on))
        self.plot_btn.clicked.connect(self._toggle_plot)
        self.add_field_btn.clicked.connect(self.add_field_after)
        self.remove_field_btn.clicked.connect(self.remove_selected_field)
        return box

    # --- the profile's format (R8.4) ---

    @property
    def is_text(self) -> bool:
        return self._text

    @property
    def pattern_error(self) -> str | None:
        """Why the Pattern typed isn't applied, while it isn't."""
        return self._pattern_error

    def set_format(self, fmt: str) -> None:
        """Lays the editor out for a profile's format: binary frames or text lines."""
        text = fmt == "text"
        self._text = text
        for widget in (self.id_spin, self.endian_combo):
            widget.setVisible(not text)
            self._row_labels[widget].setVisible(not text)
        self.pattern_edit.setVisible(text)
        self._row_labels[self.pattern_edit].setVisible(text)
        self.frame_view.setVisible(not text)
        self.line_view.setVisible(text)
        self.frame_title.setText("Line" if text else "Frame")
        self.form_title.setText("Value" if text else "Field")
        self.field_byte_title.setText("Position:" if text else "Byte:")
        self.add_field_btn.setText("Add value after" if text else "Add field after")
        self.remove_field_btn.setText("Remove value" if text else "Remove field")
        self.plot_btn.setText("Plot this value" if text else "Plot this field")
        self.tree.setHeaderLabels(
            ["Signal", "Value", "Type", "#"] if text else ["Signal", "Field", "Type", "Byte"]
        )
        self._fill_type_combo()

    def _fill_type_combo(self) -> None:
        self.field_type_combo.clear()
        types = TEXT_TYPES if self._text else [(k, v[2]) for k, v in STRUCT_TYPE_MAP.items()]
        for key, label in types:
            self.field_type_combo.addItem(label, key)

    def set_last_line(self, line: str | None) -> None:
        """The last line seen for the shown stream (received or pasted), or None."""
        self.line_view.set_line(line)

    # --- loading ---

    def set_panel_choices(self, keys: list[str]) -> None:
        """The document's panels a stream can show (`controls`), plus none."""
        self._panel_keys = list(keys)
        self.panel_combo.clear()
        self.panel_combo.addItem("(none)", None)
        for key in keys:
            self.panel_combo.addItem(key, key)

    def load(self, key: str, draft: StreamDraft) -> None:
        """Shows a stream; `draft` is edited in place (the tab keeps it)."""
        self.current_stream_key = key
        self.draft = draft
        self._typed.clear()
        self._field, self._signal = None, None
        self._pattern_error = None
        self._last_valid = None
        slots = draft.layout()
        if slots:
            self._select_field(slots[min(1, len(slots) - 1)].name)
        self.setEnabled(True)
        self.refresh()

    def load_data(self, key: str, data: StreamConfig | dict[str, Any]) -> None:
        self.load(key, StreamDraft(data))

    def get_data(self) -> tuple[str, StreamConfig]:
        """(key, stream): the draft, with any line edit still being typed applied."""
        self.flush()
        return self.key_edit.text(), self.draft.to_stream()  # type: ignore[return-value]

    def clear(self) -> None:
        self.current_stream_key = None
        self.setEnabled(False)

    def flush(self) -> None:
        """Applies line edits the user hasn't left yet (before a save or a switch)."""
        if self._flushing:
            return
        self._flushing = True
        try:
            self._flush()
        finally:
            self._flushing = False

    def _flush(self) -> None:
        for widget, handler in (
            (self.pattern_edit, self._on_pattern),
            (self.name_edit, self._on_name_edited),
            (self.time_scale_edit, self._on_time_scale),
            (self.time_step_edit, self._on_time_step),
            (self.label_edit, self._on_label),
            (self.field_name_edit, self._on_field_name),
        ):
            if widget in self._typed:
                handler()

    # --- redraw ---

    def refresh(self) -> None:
        """Redraws everything from the draft, keeping the selection."""
        self._loading = True
        try:
            self._refresh_stream_row()
            slots = self.draft.layout()
            if self._text:
                self.size_lbl.setText(f"{len(slots)} values")
                self.size_lbl.setStyleSheet("color: #888;")
                self.line_view.set_pattern(
                    self._shown_pattern(),
                    field_colors(self.draft),
                    self._field_index(),
                    dimmed=self._pattern_error is not None,
                )
            else:
                size = sum(s.size for s in slots)
                self.size_lbl.setText(f"{size} / {MAX_PAYLOAD_BYTES} B")
                self.size_lbl.setStyleSheet(
                    "color: #ff6b6b; font-weight: bold;"
                    if size > MAX_PAYLOAD_BYTES
                    else "color: #888;"
                )
                self.frame_view.set_frame(slots, field_colors(self.draft), self._field_index())
            self._rebuild_tree()
            self._refresh_form()
        finally:
            self._loading = False

    def _shown_pattern(self) -> Pattern | None:
        """The draft's pattern; while the one typed is invalid, the last valid one."""
        try:
            pattern = parse_pattern(self.draft.pattern or "")
        except PatternError:
            return self._last_valid
        self._last_valid = pattern
        return pattern

    def _refresh_stream_row(self) -> None:
        d = self.draft
        if not self.key_edit.hasFocus():
            self.key_edit.setText(self.current_stream_key or "")
        if not self.name_edit.hasFocus():
            self.name_edit.setText(d.name)
        if self._text:
            if self._pattern_error is None and not self.pattern_edit.hasFocus():
                self.pattern_edit.setText(d.pattern or "")
            self.pattern_edit.setStyleSheet(
                PATTERN_ERROR_STYLE if self._pattern_error else "QLineEdit { " + MONO_STYLE + " }"
            )
        else:
            self.id_spin.setValue(d.stream_id)
            if self.endian_combo.findText(d.endianness) < 0:
                self.endian_combo.addItem(d.endianness)  # shown as is; validation reports it
            self.endian_combo.setCurrentText(d.endianness)
        time_field = str(d.time_value("field"))
        self.time_field_combo.clear()
        for name, ftype in ((f.name, f.type) for f in d.layout()):
            if not self._text or (
                ftype in STRUCT_TYPE_MAP and STRUCT_TYPE_MAP[ftype][0] not in "fd"
            ):
                self.time_field_combo.addItem(name, name)  # text: integer values only
        if self._text:
            self.time_field_combo.addItem(LINE_NUMBER, LINE_FIELD)
        if self.time_field_combo.findData(time_field) < 0:
            self.time_field_combo.addItem(time_field, time_field)  # stale: validation says
        self.time_field_combo.setCurrentIndex(self.time_field_combo.findData(time_field))
        by_line = self._text and time_field == LINE_FIELD
        self.time_unit_lbl.setVisible(by_line)
        self.time_step_edit.setVisible(not by_line)
        self._row_labels[self.time_step_edit].setVisible(not by_line)
        if not self.time_scale_edit.hasFocus():
            self.time_scale_edit.setText(format_seconds(d.time_value("scale_s")))
        if not self.time_step_edit.hasFocus():
            self.time_step_edit.setText(str(d.time_value("step")))
        controls = d.controls
        if controls is not None and self.panel_combo.findData(controls) < 0:
            self.panel_combo.addItem(controls, controls)  # an unknown panel is kept as is
        self.panel_combo.setCurrentIndex(max(self.panel_combo.findData(controls), 0))

    def _rebuild_tree(self) -> None:
        tree = self.tree
        tree.blockSignals(True)
        tree.clear()
        slots = {s.name: s for s in self.draft.layout()}
        specs, assignment = lane_layout(self.draft.data)
        bold = QtGui.QFont(tree.font())
        bold.setBold(True)
        current: QtWidgets.QTreeWidgetItem | None = None
        lane_flags = QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsDropEnabled
        for spec in specs:
            members = [sid for sid, lane in assignment.items() if lane == spec.key]
            shown = sum(1 for sid in members if self.draft.signal(sid).get("visible", True))
            label = spec.label or DEFAULT_LANE_LABEL
            lane_item = QtWidgets.QTreeWidgetItem([label, f"{shown}/{len(members)}"])
            lane_item.setFont(0, bold)
            lane_item.setForeground(1, MUTED)
            lane_item.setData(0, ROLE_LANE, spec.key)
            lane_item.setFlags(lane_flags)
            tree.addTopLevelItem(lane_item)
            for sid in members:
                item = self._signal_item(sid, slots)
                lane_item.addChild(item)
                if sid == self._signal:
                    current = item
            lane_item.setExpanded(True)

        plotted = {str(s.get("field")) for s in self.draft.signals.values() if isinstance(s, dict)}
        unplotted = [s for s in slots.values() if s.name not in plotted]
        if unplotted:
            lane_item = QtWidgets.QTreeWidgetItem(["Not plotted", str(len(unplotted))])
            lane_item.setFont(0, bold)
            lane_item.setForeground(1, MUTED)
            lane_item.setData(0, ROLE_LANE, UNPLOTTED_LANE)
            lane_item.setFlags(lane_flags)
            tree.addTopLevelItem(lane_item)
            time_field = self.draft.time_value("field")
            for slot in unplotted:
                label = "X axis" if slot.name == time_field else ""
                item = QtWidgets.QTreeWidgetItem([label, slot.name, slot.type, self._where(slot)])
                item.setData(0, ROLE_SIGNAL, FIELD_MARK + slot.name)
                item.setData(0, ROLE_FIELD, slot.name)
                item.setFlags(
                    QtCore.Qt.ItemFlag.ItemIsEnabled
                    | QtCore.Qt.ItemFlag.ItemIsSelectable
                    | QtCore.Qt.ItemFlag.ItemIsDragEnabled
                )
                for col in (1, 2):
                    item.setForeground(col, MUTED)
                item.setForeground(3, DIM)
                lane_item.addChild(item)
                if self._signal is None and slot.name == self._field:
                    current = item
            lane_item.setExpanded(True)
        tree.blockSignals(False)
        if current is not None:
            tree.blockSignals(True)
            tree.setCurrentItem(current)
            tree.blockSignals(False)

    def _signal_item(self, sid: str, slots: dict[str, Any]) -> QtWidgets.QTreeWidgetItem:
        sig = self.draft.signal(sid)
        field = str(sig.get("field", ""))
        slot = slots.get(field)
        line = _as_dict(sig.get("line"))
        item = QtWidgets.QTreeWidgetItem(
            [
                str(sig.get("label", sid)),
                field,
                slot.type if slot else "?",
                self._where(slot) if slot else "",
            ]
        )
        item.setIcon(0, _line_icon(str(sig.get("color", "#fff")), str(line.get("style", ""))))
        item.setData(0, ROLE_SIGNAL, sid)
        item.setData(0, ROLE_FIELD, field)
        item.setFlags(
            QtCore.Qt.ItemFlag.ItemIsEnabled
            | QtCore.Qt.ItemFlag.ItemIsSelectable
            | QtCore.Qt.ItemFlag.ItemIsUserCheckable
            | QtCore.Qt.ItemFlag.ItemIsDragEnabled
            | QtCore.Qt.ItemFlag.ItemIsDropEnabled
        )
        visible = sig.get("visible", True)
        item.setCheckState(
            0, QtCore.Qt.CheckState.Checked if visible else QtCore.Qt.CheckState.Unchecked
        )
        item.setToolTip(0, "Checked: shown when the stream opens")
        for col in (1, 2):
            item.setForeground(col, MUTED)
        item.setForeground(3, DIM)
        return item

    def _where(self, slot: Any) -> str:
        """The tree's last column: a value's position (text), a field's byte offset."""
        return str(slot.index + 1) if self._text else str(slot.offset)

    def _refresh_form(self) -> None:
        index = self._field_index()
        slot = next((s for s in self.draft.layout() if s.index == index), None)
        for w in (self.field_name_edit, self.field_type_combo, self.add_field_btn):
            w.setEnabled(slot is not None)
        self.remove_field_btn.setEnabled(slot is not None)
        self.plot_btn.setEnabled(slot is not None)
        if slot is None:
            self.field_name_edit.setText("")
            self.field_byte_lbl.setText("")
            self.signal_box.hide()
            self.not_plotted_lbl.hide()
            return
        if not self.field_name_edit.hasFocus():
            self.field_name_edit.setText(slot.name)
        if self.field_type_combo.findData(slot.type) < 0:
            self.field_type_combo.addItem(slot.type, slot.type)  # unknown: shown as is
        self.field_type_combo.setCurrentIndex(self.field_type_combo.findData(slot.type))
        if self._text:
            self.field_byte_lbl.setText(f"{slot.index + 1} of {len(self.draft.fields)}")
        else:
            self.field_byte_lbl.setText(f"{slot.offset} (0x{slot.offset:02x}), {slot.size} B")

        sig = self.draft.signal(self._signal) if self._signal else None
        self.signal_box.setVisible(sig is not None)
        is_time = slot.name == self.draft.time_value("field")
        self.not_plotted_lbl.setText("The X axis; not plotted." if is_time else "Not plotted.")
        self.not_plotted_lbl.setVisible(sig is None)
        thing = "value" if self._text else "field"
        self.plot_btn.setText("Stop plotting" if sig is not None else f"Plot this {thing}")
        if sig is None:
            self.label_edit.setText("")
            return
        if not self.label_edit.hasFocus():
            self.label_edit.setText(str(sig.get("label", "")))
        self._fill_lane_combo(str(sig.get("group", DEFAULT_LANE)))
        self.color_btn.set_color(str(sig.get("color", "#FFFFFF")))
        line = _as_dict(sig.get("line"))
        style = str(line.get("style", "solid"))
        if self.style_combo.findText(style) < 0:
            self.style_combo.addItem(style)
        self.style_combo.setCurrentText(style)
        width = line.get("width", 1)
        self.width_spin.setValue(int(width) if isinstance(width, int | float) else 1)
        self.shown_chk.setChecked(bool(sig.get("visible", True)))

    def _lanes(self) -> list[tuple[str, str]]:
        """(key, label) of every lane: in use (display order), then described but unused."""
        specs, _ = lane_layout(self.draft.data)
        lanes = [(s.key, s.label or DEFAULT_LANE_LABEL) for s in specs]
        if not any(k == DEFAULT_LANE for k, _ in lanes):
            lanes.insert(0, (DEFAULT_LANE, DEFAULT_LANE_LABEL))
        groups = self.draft.data.get("groups")
        for key in groups if isinstance(groups, dict) else ():
            if not any(k == key for k, _ in lanes):
                lanes.append((key, self.draft.lane_label(key) or key))
        return lanes

    def _fill_lane_combo(self, lane: str) -> None:
        self.lane_combo.clear()
        for key, label in self._lanes():
            self.lane_combo.addItem(label, key)
        self.lane_combo.setCurrentIndex(max(self.lane_combo.findData(lane), 0))

    # --- selection ---

    def _field_index(self) -> int | None:
        names = self.draft.field_names()
        return names.index(self._field) if self._field in names else None

    def _select_field(self, field: str | None, signal: str | None = None) -> None:
        self._field = field
        if signal is None and field is not None:
            signal = next(iter(self.draft.signals_of_field(field)), None)
        self._signal = signal

    @property
    def selected_field(self) -> str | None:
        return self._field

    @property
    def selected_signal(self) -> str | None:
        return self._signal

    def select(self, field: str, signal: str | None = None) -> None:
        self.flush()
        self._select_field(field, signal)
        self.refresh()

    def _on_frame_clicked(self, index: int) -> None:
        self.select(self.draft.field_names()[index])

    def _on_tree_current(
        self, current: QtWidgets.QTreeWidgetItem | None, _previous: Any = None
    ) -> None:
        if current is None or self._loading:
            return
        field = current.data(0, ROLE_FIELD)
        if not isinstance(field, str):
            return  # a lane row
        sid = current.data(0, ROLE_SIGNAL)
        signal = None if not isinstance(sid, str) or sid.startswith(FIELD_MARK) else sid
        self.flush()
        self._field, self._signal = field, signal
        self._loading = True
        try:
            self.frame_view.set_selected(self._field_index())
            self.line_view.set_selected(self._field_index())
            self._refresh_form()
        finally:
            self._loading = False

    # --- edits ---

    def _edited(self) -> None:
        self.flush()  # text still being typed elsewhere isn't overwritten by the redraw
        self.refresh()
        self.changed.emit()

    def _on_key_edited(self) -> None:
        text = self.key_edit.text().strip()
        if self.current_stream_key is not None and text and text != self.current_stream_key:
            self.key_rename_requested.emit(text)

    def _on_name_edited(self) -> None:
        if self._loading or self.current_stream_key is None:
            return
        if self.name_edit.text() != self.draft.name:
            self.draft.name = self.name_edit.text()
            self.changed.emit()

    def _on_id_changed(self, value: int) -> None:
        if not self._loading and value != self.draft.stream_id:
            self.draft.stream_id = value
            self.changed.emit()

    def _on_endianness(self, _index: int) -> None:
        if self.endian_combo.currentText() != self.draft.endianness:
            self.draft.endianness = self.endian_combo.currentText()
            self.changed.emit()

    def _on_time_field(self, _index: int) -> None:
        self.draft.set_time("field", str(self.time_field_combo.currentData()))
        self._edited()

    def _on_pattern(self) -> None:
        if self._loading or self.current_stream_key is None or not self._text:
            return
        text = self.pattern_edit.text()
        self._typed.discard(self.pattern_edit)
        if text == self.draft.pattern:
            if self._pattern_error is not None:
                self._pattern_error = None
                self._edited()
            return
        error = self.draft.set_pattern(text)
        self._pattern_error = error
        if error is not None:
            self.refresh()
            self.problem.emit(f"Pattern: {error}")
            return
        if self._field not in self.draft.field_names():
            names = self.draft.field_names()
            self._select_field(names[0] if names else None)
        self._edited()

    def _revert_pattern(self) -> None:
        """Esc in the Pattern field: back to the stream's pattern."""
        self._typed.discard(self.pattern_edit)
        self._pattern_error = None
        self.pattern_edit.setText(self.draft.pattern or "")
        self.refresh()
        self.changed.emit()  # the status line drops the error

    def _on_time_scale(self) -> None:
        if self._loading or self.current_stream_key is None:
            return
        text = self.time_scale_edit.text()
        current = self.draft.time_value("scale_s")
        if text.strip() == format_seconds(current):
            return
        value = parse_seconds(text)
        if value is None:
            self.problem.emit(f"'{text}' is not a time per tick (e.g. 5 ms, 1 µs, 0.005)")
            self.time_scale_edit.setText(format_seconds(current))
            return
        if value != current:
            self.draft.set_time("scale_s", value)
            self.changed.emit()
        self.time_scale_edit.setText(format_seconds(value))

    def _on_time_step(self) -> None:
        if self._loading or self.current_stream_key is None:
            return
        text = self.time_step_edit.text()
        current = self.draft.time_value("step")
        if text.strip() == str(current):
            return
        value = _parse_number(text)
        if value is None or value <= 0:
            self.problem.emit(f"'{text}' is not a step (a positive number, 1 for a counter)")
            self.time_step_edit.setText(str(current))
            return
        self.draft.set_time("step", value)
        self.changed.emit()

    def _on_panel(self, _index: int) -> None:
        controls = self.panel_combo.currentData()
        self.draft.controls = controls if isinstance(controls, str) else None
        self.changed.emit()

    def _on_field_name(self) -> None:
        index = self._field_index()
        if self._loading or index is None:
            return
        text = self.field_name_edit.text().strip()
        if text == self._field:
            return
        error = self.draft.rename_field(index, text)
        if error is not None:
            self.problem.emit(error)
            self.field_name_edit.setText(self._field or "")
            return
        self._field = text
        self._edited()

    def _on_field_type(self, _index: int) -> None:
        index = self._field_index()
        if index is not None:
            self.draft.set_field_type(index, str(self.field_type_combo.currentData()))
            self._edited()

    def _on_label(self) -> None:
        if self._loading or self._signal is None:
            return
        text = self.label_edit.text()
        if text != self.draft.signal(self._signal).get("label"):
            self.draft.set_signal(self._signal, "label", text)
            self._edited()

    def _set_signal(self, attr: str, value: Any) -> None:
        if self._loading or self._signal is None:
            return
        if attr in ("style", "width"):
            line = self.draft.signal(self._signal).get("line")
            old = line.get(attr) if isinstance(line, dict) else None
        else:
            old = self.draft.signal(self._signal).get(attr)
        if old != value:
            self.draft.set_signal(self._signal, attr, value)
            self._edited()

    def _on_lane_picked(self, index: int) -> None:
        lane = self.lane_combo.itemData(index)
        if isinstance(lane, str):
            self._set_signal("group", lane)

    def _on_lane_typed(self) -> None:
        if self._loading or self._signal is None:
            return
        text = self.lane_combo.currentText().strip()
        if not text:
            return
        for key, label in self._lanes():
            if text in (label, key):
                self._set_signal("group", key)
                return
        key = self.new_lane(text)
        self._set_signal("group", key)

    def new_lane(self, label: str | None = None) -> str:
        """Adds a lane (named `label`, else "Lane N"); returns its key."""
        taken = {k for k, _ in self._lanes()}
        if label is None:
            n = len(taken) + 1
            while f"lane{n}" in taken:
                n += 1
            label = f"Lane {n}"
        key = unique_name(label.lower(), taken, fallback="lane")
        self.draft.set_lane_label(key, label)
        return key

    def _on_tree_item_changed(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        sid = item.data(0, ROLE_SIGNAL)
        if column != 0 or not isinstance(sid, str) or sid.startswith(FIELD_MARK):
            return
        visible = item.checkState(0) == QtCore.Qt.CheckState.Checked
        if visible != self.draft.signal(sid).get("visible", True):
            self.draft.set_signal(sid, "visible", visible)
            self._signal, self._field = sid, str(self.draft.signal(sid).get("field"))
            self._edited()

    def _on_dropped(self, sid: str, lane: str) -> None:
        """A drag onto a lane: plot a field there, move a signal, or stop plotting it."""
        if sid.startswith(FIELD_MARK):
            if lane == UNPLOTTED_LANE:
                return
            field = sid[len(FIELD_MARK) :]
            lane = self.new_lane() if lane == NEW_LANE else lane
            self._field, self._signal = field, self.draft.add_signal(field, lane or None)
        elif lane == UNPLOTTED_LANE:
            field = str(self.draft.signal(sid).get("field"))
            self.draft.remove_signal(sid)
            self._select_field(field)
        else:
            lane = self.new_lane() if lane == NEW_LANE else lane
            self.draft.set_signal(sid, "group", lane)
            self._signal = sid
        self._edited()

    def _toggle_plot(self) -> None:
        if self._field is None:
            return
        if self._signal is not None:
            self.draft.remove_signal(self._signal)
            self._select_field(self._field)
        else:
            self._signal = self.draft.add_signal(self._field)
        self._edited()

    def add_field_after(self) -> None:
        index = self._field_index()
        if self._text:
            at = self.draft.add_value_after(index)
        else:
            at = (index + 1) if index is not None else len(self.draft.fields)
            at = self.draft.add_field(at)
        self._select_field(self.draft.field_names()[at])
        self._edited()
        self.field_name_edit.setFocus()
        self.field_name_edit.selectAll()

    def remove_selected_field(self) -> None:
        index = self._field_index()
        if index is None:
            return
        if self._text:
            error = self.draft.remove_value(index)
            if error is not None:
                self.problem.emit(error)
                return
        else:
            self.draft.remove_field(index)
        names = self.draft.field_names()
        self._select_field(names[min(index, len(names) - 1)] if names else None)
        self._edited()

    def move_selected_field(self, delta: int) -> None:
        index = self._field_index()
        if index is not None:
            self.draft.move_field(index, index + delta)
            self._edited()

    # --- menus ---

    def _field_menu(self) -> QtWidgets.QMenu:
        menu = QtWidgets.QMenu(self)
        thing = "value" if self._text else "field"
        # A text value's place is its place in the pattern: edit the pattern to move it.
        moves: tuple[tuple[str | None, Any], ...] = (
            ()
            if self._text
            else (
                ("Move earlier", lambda: self.move_selected_field(-1)),
                ("Move later", lambda: self.move_selected_field(1)),
            )
        )
        for text, slot in (
            ("Stop plotting" if self._signal else f"Plot this {thing}", self._toggle_plot),
            (None, None),
            (f"Add {thing} after", self.add_field_after),
            *moves,
            (None, None),
            (f"Remove {thing}", self.remove_selected_field),
        ):
            if text is None or slot is None:
                menu.addSeparator()
                continue
            action = menu.addAction(text)
            assert action is not None
            action.triggered.connect(slot)
        return menu

    def _show_field_menu(self, _index: int, pos: QtCore.QPoint) -> None:
        self._field_menu().exec(pos)

    def _on_tree_menu(self, pos: QtCore.QPoint) -> None:
        item = self.tree.itemAt(pos)
        viewport = self.tree.viewport()
        if item is None or viewport is None:
            return
        global_pos = viewport.mapToGlobal(pos)
        lane = item.data(0, ROLE_LANE)
        if isinstance(lane, str):
            if lane not in (UNPLOTTED_LANE, DEFAULT_LANE):  # the default lane has no entry
                menu = QtWidgets.QMenu(self)
                action = menu.addAction("Rename lane…")
                assert action is not None
                action.triggered.connect(lambda: self.rename_lane(lane))
                menu.exec(global_pos)
            return
        self._on_tree_current(item)
        self._field_menu().exec(global_pos)

    def rename_lane(self, lane: str, label: str | None = None) -> None:
        if label is None:
            current = self.draft.lane_label(lane) or lane or DEFAULT_LANE_LABEL
            label, ok = QtWidgets.QInputDialog.getText(self, "Rename lane", "Lane:", text=current)
            if not ok:
                return
        if label.strip():
            self.draft.set_lane_label(lane, label.strip())
            self._edited()
