"""
Stream editor (R7.1, R11): one stream, in the scope look of the main window.

- One row of stream settings in words: key, name, ID, byte order (`LE | BE`), the X axis and
  time per tick, and the Tune panel shown with it.
- The frame as one strip (`FrameStrip`): what the device sends, in wire order.
- The signals as the main window's Signals pane lists them (`FieldTree`): lanes, one row per
  left/right pair, each side with its swatch (shown at open), field and byte, then the fields
  that aren't plotted. Dragging a field or row onto a lane plots or moves it there; onto
  "Not plotted" stops plotting it.
- The inspector: the selected signal (`SIGNAL`) and its field (`FIELD`), with the actions in
  their section headers. `L=R` (on by default) gives a pair's other side the same lane, colour
  and line width, as the Tune pane's `L=R` does for gains.

In a text profile (R8.4) a stream is a line pattern: Pattern replaces ID and byte order,
`LineView` replaces the strip, and the inspector edits a value (its slot in the pattern).

Every edit is an operation on a `StreamDraft` (`core/config/draft.py`), so the editor
never rebuilds a stream from its widgets and can't lose what it doesn't show (C4).
"""

from __future__ import annotations

import re
from typing import Any

from PyQt6 import QtCore, QtGui, QtWidgets

from core.config import ENDIANNESS
from core.config.draft import LINE_STYLES, StreamDraft, unique_name
from core.protocol.constants import STRUCT_TYPE_MAP
from core.protocol.text_line import LINE_FIELD, Pattern, PatternError, parse_pattern
from core.types import StreamConfig
from styles import (
    BORDER,
    BORDER_DIM,
    MONO_CSS,
    PANEL,
    TEXT,
    TEXT_BRIGHT,
    TEXT_DISABLED,
    TEXT_MUTED,
)
from ui.charts.lanes import DEFAULT_LANE, lane_layout
from ui.common.color_button import ColorButton
from ui.config.frame_strip import FrameStrip
from ui.config.frame_view import TIME_FIELD
from ui.config.line_view import LineView
from ui.config.signal_list import FIELD_MARK, ROLE_FIELD, UNPLOTTED_LANE, FieldTree
from ui.panels.signal_rows import pair_rows
from ui.panels.signals import DEFAULT_LANE_LABEL, NEW_LANE, ROLE_LANE, SWATCH_PX, draw_swatch

__all__ = ["FIELD_MARK", "ROLE_FIELD", "UNPLOTTED_LANE", "StreamEditor"]

LINE_NUMBER = "(line number)"  # the X axis of a text stream without a counter
# The value types the editor offers for text; a file's other types are shown as they are.
TEXT_TYPES = (("f32", "number"), ("u32", "integer"), ("i32", "signed integer"))
MONO_STYLE = MONO_CSS
PATTERN_ERROR_STYLE = "QLineEdit { border: 1px solid #FF4040; " + MONO_STYLE + " }"
LINE_WIDTHS = (1, 2, 3)
LINKED = ("group", "color", "width")  # what L=R gives the other side
INSPECTOR_WIDTH = 300
ROW_BG = "#0a0a0a"
MUTED_LABEL = "#8a8a8a"

WORD_BUTTON = (
    f"QPushButton, QToolButton {{ background: transparent; border: none; color: {MUTED_LABEL};"
    " padding: 0; font-size: 11px; }"
    f" QPushButton:hover, QToolButton:hover {{ color: {TEXT_BRIGHT}; background: transparent; }}"
    f" QPushButton:disabled {{ color: {TEXT_DISABLED}; }}"
)
SEGMENT = (
    "QPushButton { background: transparent; border: none; border-right: 1px solid #3a3a3a;"
    f" color: {MUTED_LABEL}; padding: 0 9px; font-size: 11px; min-height: 20px; }}"
    " QPushButton:last-child { border-right: none; }"
    f" QPushButton:checked {{ background: {TEXT}; color: #000; font-weight: bold; }}"
    " QPushButton:hover:!checked { background: #1c1c1c; }"
)
LINE_SEGMENT = SEGMENT.replace(
    f"QPushButton:checked {{ background: {TEXT}; color: #000; font-weight: bold; }}",
    "QPushButton:checked { background: #2a2a2a; }",
)
LINK_ON = (
    f"QPushButton {{ background: {TEXT}; border: 1px solid {TEXT}; color: #000;"
    " font-weight: bold; font-size: 11px; padding: 0 6px; min-height: 18px; }"
)
LINK_OFF = (
    f"QPushButton {{ background: #000; border: 1px solid {TEXT_DISABLED}; color: {MUTED_LABEL};"
    " font-weight: bold; font-size: 11px; padding: 0 6px; min-height: 18px; }"
)


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
    pen = QtGui.QPen(QtGui.QColor(color), 2)
    pen.setStyle(
        {"dashed": QtCore.Qt.PenStyle.DashLine, "dotted": QtCore.Qt.PenStyle.DotLine}.get(
            style, QtCore.Qt.PenStyle.SolidLine
        )
    )
    painter.setPen(pen)
    painter.drawLine(1, 6, 21, 6)
    painter.end()
    return QtGui.QIcon(pixmap)


def _swatch_pixmap(color: str, shown: bool, dashed: bool) -> QtGui.QPixmap:
    pixmap = QtGui.QPixmap(SWATCH_PX, SWATCH_PX)
    pixmap.fill(QtGui.QColor(0, 0, 0, 0))
    painter = QtGui.QPainter(pixmap)
    draw_swatch(painter, QtCore.QRectF(0, 0, SWATCH_PX, SWATCH_PX), color, shown, dashed)
    painter.end()
    return pixmap


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


def plotted_fields(draft: StreamDraft) -> tuple[dict[str, str], frozenset[str]]:
    """(field -> its first signal's colour, fields whose signals are all hidden at open)."""
    colors: dict[str, str] = {}
    shown: set[str] = set()
    for sig in draft.signals.values():
        if isinstance(sig, dict) and isinstance(sig.get("field"), str):
            colors.setdefault(sig["field"], str(sig.get("color", "#ffffff")))
            if sig.get("visible", True):
                shown.add(sig["field"])
    return colors, frozenset(f for f in colors if f not in shown)


class PatternEdit(QtWidgets.QLineEdit):
    """The Pattern field: Esc drops what was typed (a pattern is applied on leaving)."""

    escaped = QtCore.pyqtSignal()

    def keyPressEvent(self, event: QtGui.QKeyEvent | None) -> None:  # noqa: N802
        if event is not None and event.key() == QtCore.Qt.Key.Key_Escape:
            self.escaped.emit()
            return
        super().keyPressEvent(event)


class SwatchToggle(QtWidgets.QPushButton):
    """Shown when the stream opens: the signal's swatch (filled or hollow) and a few words."""

    def __init__(self, text: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._color = TEXT
        self._dashed = False
        self.setStyleSheet(
            "QPushButton { background: transparent; border: none; text-align: left;"
            f" padding: 0 0 0 20px; color: {MUTED_LABEL}; min-height: 24px; }}"
            f" QPushButton:checked {{ background: transparent; color: {MUTED_LABEL}; }}"
            f" QPushButton:hover {{ color: {TEXT}; background: transparent; }}"
        )
        self.toggled.connect(lambda _on: self.update())

    def set_swatch(self, color: str, dashed: bool) -> None:
        self._color, self._dashed = color, dashed
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent | None) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        y = self.height() / 2 - SWATCH_PX / 2
        box = QtCore.QRectF(0, y, SWATCH_PX, SWATCH_PX)
        draw_swatch(painter, box, self._color, self.isChecked(), self._dashed)
        painter.end()


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
        layout.setSpacing(0)
        layout.addWidget(self._build_stream_row())

        frame_box = QtWidgets.QWidget()
        frame_box.setObjectName("frame_box")
        frame_box.setStyleSheet(
            f"QWidget#frame_box {{ background: #000; border-bottom: 1px solid {BORDER_DIM}; }}"
        )
        frame_layout = QtWidgets.QVBoxLayout(frame_box)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(0)
        self.frame_strip = FrameStrip()
        frame_layout.addWidget(self.frame_strip)
        self.line_view = LineView()
        self.line_box = QtWidgets.QWidget()  # the margins go with the view
        line_box = QtWidgets.QHBoxLayout(self.line_box)
        line_box.setContentsMargins(12, 8, 12, 8)
        line_box.addWidget(self.line_view)
        frame_layout.addWidget(self.line_box)
        layout.addWidget(frame_box)

        body = QtWidgets.QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.tree = FieldTree()
        body.addWidget(self.tree, 1)
        body.addWidget(self._build_inspector())
        layout.addLayout(body, 1)

        self.key_edit.editingFinished.connect(self._on_key_edited)
        self.name_edit.editingFinished.connect(self._on_name_edited)
        self.id_spin.valueChanged.connect(self._on_id_changed)
        self.order_group.idClicked.connect(self._on_endianness)
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
        self.frame_strip.field_clicked.connect(self._on_frame_clicked)
        self.frame_strip.menu_requested.connect(self._show_field_menu)
        self.line_view.field_clicked.connect(self._on_frame_clicked)
        self.line_view.menu_requested.connect(self._show_field_menu)
        self.tree.picked.connect(self._on_picked)
        self.tree.toggled.connect(self._on_toggled)
        self.tree.dropped.connect(self._on_dropped)
        self.tree.customContextMenuRequested.connect(self._on_tree_menu)

    def _build_stream_row(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QWidget()
        bar.setObjectName("stream_row")
        bar.setFixedHeight(36)
        bar.setStyleSheet(
            f"QWidget#stream_row {{ background: {ROW_BG}; border-bottom: 1px solid {BORDER_DIM}; }}"
            f" QWidget#stream_row QLabel {{ color: {MUTED_LABEL}; }}"
        )
        row = QtWidgets.QHBoxLayout(bar)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(7)
        self.key_edit = QtWidgets.QLineEdit()
        self.key_edit.setFixedWidth(100)
        self.key_edit.setStyleSheet("QLineEdit { " + MONO_STYLE + " }")
        self.key_edit.setToolTip("The stream's key in streams.json")
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setFixedWidth(160)
        self.id_spin = HexSpinBox()
        self.id_spin.setFixedWidth(52)
        self.id_spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.id_spin.setToolTip("The frame's TYPE byte")
        self.order_box, self.order_group = self._segment(
            [("LE", "Little-endian"), ("BE", "Big-endian")], SEGMENT
        )
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
        self.time_scale_edit.setFixedWidth(60)
        self.time_scale_edit.setToolTip(
            "Time per tick of the X-axis field: the MCU loop period for a loop counter "
            "(5 ms), or 1 µs for a microsecond timestamp"
        )
        self.time_step_edit = QtWidgets.QLineEdit()
        self.time_step_edit.setFixedWidth(36)
        self.time_step_edit.setToolTip(
            "How much the X-axis field goes up per frame: 1 for a loop counter. A larger "
            "jump is drawn as a gap (lost frames)."
        )
        self.time_unit_lbl = QtWidgets.QLabel("per line")
        self.panel_combo = QtWidgets.QComboBox()  # the stream's `controls` panel (R5.2)
        self.panel_combo.setToolTip("The Tune pane shown with this stream")
        self.set_panel_choices([])
        self._row_labels: dict[QtWidgets.QWidget, QtWidgets.QLabel] = {}
        for label, widget in (
            ("Key", self.key_edit),
            ("Name", self.name_edit),
            ("ID", self.id_spin),
            ("Order", self.order_box),
            ("Pattern", self.pattern_edit),
            ("X", self.time_field_combo),
            ("×", self.time_scale_edit),
            ("step", self.time_step_edit),
            ("Tune", self.panel_combo),
        ):
            lbl = QtWidgets.QLabel(label)
            if label not in ("Key", "×", "step"):
                lbl.setContentsMargins(11, 0, 0, 0)
            row.addWidget(lbl)
            row.addWidget(widget)
            self._row_labels[widget] = lbl
            if widget is self.time_scale_edit:
                row.addWidget(self.time_unit_lbl)
        row.addStretch()
        return bar

    @staticmethod
    def _segment(
        options: list[tuple[str, str]], style: str
    ) -> tuple[QtWidgets.QFrame, QtWidgets.QButtonGroup]:
        """A row of exclusive square buttons in one 1 px box (Manual | Live, LE | BE)."""
        box = QtWidgets.QFrame()
        box.setObjectName("segment")
        box.setStyleSheet(
            "QFrame#segment { border: 1px solid #3a3a3a; background: transparent; } " + style
        )
        row = QtWidgets.QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        group = QtWidgets.QButtonGroup(box)
        group.setExclusive(True)
        for i, (text, tip) in enumerate(options):
            btn = QtWidgets.QPushButton(text)
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
            row.addWidget(btn)
            group.addButton(btn, i)
        return box, group

    @staticmethod
    def _section(
        title: str, *actions: QtWidgets.QWidget
    ) -> tuple[QtWidgets.QWidget, QtWidgets.QLabel]:
        """A section header: small caps, a hairline, then quiet word actions."""
        head = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(head)
        row.setContentsMargins(12, 14, 12, 6)
        row.setSpacing(10)
        lbl = QtWidgets.QLabel(title)
        lbl.setStyleSheet(
            f"color: {MUTED_LABEL}; font-size: 11px; font-weight: bold; letter-spacing: 1px;"
        )
        line = QtWidgets.QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {BORDER_DIM};")
        row.addWidget(lbl)
        row.addWidget(line, 1)
        for action in actions:
            action.setStyleSheet(WORD_BUTTON)
            action.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            row.addWidget(action)
        return head, lbl

    @staticmethod
    def _grid() -> tuple[QtWidgets.QWidget, QtWidgets.QGridLayout]:
        box = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(box)
        grid.setContentsMargins(12, 0, 12, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        grid.setColumnMinimumWidth(0, 56)
        grid.setColumnStretch(1, 1)
        return box, grid

    @staticmethod
    def _grid_row(grid: QtWidgets.QGridLayout, label: str, widget: Any) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(label)
        lbl.setStyleSheet(f"color: {MUTED_LABEL};")
        r = grid.rowCount()
        grid.addWidget(lbl, r, 0)
        if isinstance(widget, QtWidgets.QLayout):
            grid.addLayout(widget, r, 1)
        else:
            grid.addWidget(widget, r, 1)
        return lbl

    def _build_inspector(self) -> QtWidgets.QWidget:
        box = QtWidgets.QWidget()
        box.setObjectName("inspector")
        box.setFixedWidth(INSPECTOR_WIDTH)
        box.setStyleSheet(
            f"QWidget#inspector {{ background: {PANEL}; border-left: 1px solid {BORDER}; }}"
        )
        outer = QtWidgets.QVBoxLayout(box)
        outer.setContentsMargins(0, 0, 0, 12)
        outer.setSpacing(0)

        # --- header: the selection, in its colour ---
        head = QtWidgets.QWidget()
        head.setObjectName("inspector_head")
        head.setStyleSheet(f"QWidget#inspector_head {{ border-bottom: 1px solid {BORDER_DIM}; }}")
        hrow = QtWidgets.QHBoxLayout(head)
        hrow.setContentsMargins(12, 12, 12, 10)
        hrow.setSpacing(10)
        self.head_swatch = QtWidgets.QLabel()
        self.head_swatch.setFixedSize(SWATCH_PX, SWATCH_PX)
        titles = QtWidgets.QVBoxLayout()
        titles.setSpacing(3)
        self.title_lbl = QtWidgets.QLabel("")
        self.sub_lbl = QtWidgets.QLabel("")
        self.sub_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; {MONO_STYLE}")
        titles.addWidget(self.title_lbl)
        titles.addWidget(self.sub_lbl)
        self.link_btn = QtWidgets.QPushButton("L=R")
        self.link_btn.setCheckable(True)
        self.link_btn.setChecked(True)
        self.link_btn.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.link_btn.setToolTip(
            "Lane, colour and line width apply to both sides of the pair (R stays dashed)"
        )
        self.link_btn.toggled.connect(self._style_link)
        self._style_link(True)
        hrow.addWidget(self.head_swatch, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        hrow.addLayout(titles, 1)
        hrow.addWidget(self.link_btn, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        outer.addWidget(head)

        # --- SIGNAL ---
        self.unplot_btn = QtWidgets.QPushButton("Stop plotting")
        self.unplot_btn.setToolTip("Keep the field, drop its signal (or drag it to Not plotted)")
        self.signal_head, self.signal_title = self._section("SIGNAL", self.unplot_btn)
        outer.addWidget(self.signal_head)
        self.signal_box, sgrid = self._grid()
        self.label_edit = QtWidgets.QLineEdit()
        self.lane_combo = QtWidgets.QComboBox()
        self.lane_combo.setEditable(True)
        self.lane_combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.lane_combo.setToolTip("Pick a lane, or type a new lane's name")
        self.color_btn = ColorButton()
        line = QtWidgets.QHBoxLayout()
        line.setSpacing(8)
        self.style_box, self.style_group = self._segment(
            [("", s.capitalize()) for s in LINE_STYLES], LINE_SEGMENT
        )
        self.width_box, self.width_group = self._segment(
            [(str(w), f"{w} px") for w in LINE_WIDTHS], SEGMENT
        )
        line.addWidget(self.style_box)
        line.addWidget(self.width_box)
        line.addStretch()
        self.shown_chk = SwatchToggle("when the stream opens")
        self._grid_row(sgrid, "Label", self.label_edit)
        self._grid_row(sgrid, "Lane", self.lane_combo)
        self._grid_row(sgrid, "Colour", self.color_btn)
        self._grid_row(sgrid, "Line", line)
        self._grid_row(sgrid, "Shown", self.shown_chk)
        outer.addWidget(self.signal_box)

        self.loose_box = QtWidgets.QWidget()
        lrow = QtWidgets.QHBoxLayout(self.loose_box)
        lrow.setContentsMargins(12, 0, 12, 0)
        self.not_plotted_lbl = QtWidgets.QLabel("Not plotted.")
        self.not_plotted_lbl.setStyleSheet(f"color: {TEXT_MUTED};")
        self.plot_btn = QtWidgets.QToolButton()
        self.plot_btn.setText("Plot")
        self.plot_btn.setToolTip("Plot in the first lane; the arrow picks another")
        self.plot_btn.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.plot_btn.setStyleSheet(
            f"QToolButton {{ background: {TEXT}; color: #000; border: 1px solid {TEXT};"
            " font-weight: bold; padding: 2px 10px; }"
            " QToolButton::menu-button { border-left: 1px solid #000; width: 16px; }"
        )
        self.plot_menu = QtWidgets.QMenu(self.plot_btn)
        self.plot_menu.aboutToShow.connect(self._fill_plot_menu)
        self.plot_btn.setMenu(self.plot_menu)
        lrow.addWidget(self.not_plotted_lbl, 1)
        lrow.addWidget(self.plot_btn)
        outer.addWidget(self.loose_box)

        # --- FIELD ---
        self.add_field_btn = QtWidgets.QPushButton("Insert after")
        self.add_field_btn.setToolTip("Insert a field after this one")
        self.remove_field_btn = QtWidgets.QPushButton("Remove")
        self.remove_field_btn.setToolTip("Remove this field and its signals")
        field_head, self.form_title = self._section(
            "FIELD", self.add_field_btn, self.remove_field_btn
        )
        outer.addWidget(field_head)
        field_box, fgrid = self._grid()
        self.field_name_edit = QtWidgets.QLineEdit()
        self.field_name_edit.setStyleSheet("QLineEdit { " + MONO_STYLE + " }")
        self.field_type_combo = QtWidgets.QComboBox()
        self._fill_type_combo()
        self.field_byte_lbl = QtWidgets.QLabel("")
        self._grid_row(fgrid, "Name", self.field_name_edit)
        self._grid_row(fgrid, "Type", self.field_type_combo)
        self.field_byte_title = self._grid_row(fgrid, "Byte", self.field_byte_lbl)
        outer.addWidget(field_box)
        outer.addStretch()

        self.field_name_edit.editingFinished.connect(self._on_field_name)
        self.field_type_combo.activated.connect(self._on_field_type)
        self.label_edit.editingFinished.connect(self._on_label)
        self.lane_combo.activated.connect(self._on_lane_picked)
        lane_edit = self.lane_combo.lineEdit()
        assert lane_edit is not None
        lane_edit.editingFinished.connect(self._on_lane_typed)
        self.color_btn.colorChanged.connect(lambda c: self._set_signal("color", c))
        self.style_group.idClicked.connect(lambda i: self._set_signal("style", LINE_STYLES[i]))
        self.width_group.idClicked.connect(lambda i: self._set_signal("width", LINE_WIDTHS[i]))
        self.shown_chk.clicked.connect(lambda on: self._set_signal("visible", on))
        self.unplot_btn.clicked.connect(self._toggle_plot)
        self.plot_btn.clicked.connect(self._toggle_plot)
        self.add_field_btn.clicked.connect(self.add_field_after)
        self.remove_field_btn.clicked.connect(self.remove_selected_field)
        return box

    def _style_link(self, on: bool) -> None:
        self.link_btn.setStyleSheet(LINK_ON if on else LINK_OFF)

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
        for widget in (self.id_spin, self.order_box):
            widget.setVisible(not text)
            self._row_labels[widget].setVisible(not text)
        self.pattern_edit.setVisible(text)
        self._row_labels[self.pattern_edit].setVisible(text)
        self.frame_strip.setVisible(not text)
        self.line_view.setVisible(text)
        self.line_box.setVisible(text)
        self.form_title.setText("VALUE" if text else "FIELD")
        self.field_byte_title.setText("Position" if text else "Byte")
        thing = "value" if text else "field"
        self.add_field_btn.setToolTip(f"Insert a {thing} after this one")
        self.remove_field_btn.setToolTip(f"Remove this {thing} and its signals")
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
        self.panel_combo.addItem("none", None)
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
                self.line_view.set_pattern(
                    self._shown_pattern(),
                    field_colors(self.draft),
                    self._field_index(),
                    dimmed=self._pattern_error is not None,
                )
            else:
                colors, hidden = plotted_fields(self.draft)
                self.frame_strip.set_frame(slots, colors, self._field_index(), hidden)
            specs, assignment = lane_layout(self.draft.data)
            time_field = self.draft.time_value("field")
            self.tree.show_stream(
                specs,
                assignment,
                self._signal_dicts(),
                slots,
                time_field if isinstance(time_field, str) else None,
                self._text,
            )
            self.tree.select(self._selection_key())
            self._refresh_form()
        finally:
            self._loading = False

    def _signal_dicts(self) -> dict[str, dict[str, Any]]:
        return {k: v for k, v in self.draft.signals.items() if isinstance(v, dict)}

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
            order = ENDIANNESS.index(d.endianness) if d.endianness in ENDIANNESS else -1
            self.order_group.setExclusive(False)  # an unknown order: neither (validation says)
            for i, btn in enumerate(self.order_group.buttons()):
                btn.setChecked(i == order)
            self.order_group.setExclusive(True)
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

    def _refresh_form(self) -> None:
        index = self._field_index()
        slot = next((s for s in self.draft.layout() if s.index == index), None)
        for w in (self.field_name_edit, self.field_type_combo, self.add_field_btn):
            w.setEnabled(slot is not None)
        self.remove_field_btn.setEnabled(slot is not None)
        self.plot_btn.setEnabled(slot is not None)
        sig = self.draft.signal(self._signal) if self._signal else None
        self.signal_box.setVisible(sig is not None)
        self.unplot_btn.setVisible(sig is not None)
        self.loose_box.setVisible(sig is None and slot is not None)
        self.link_btn.setVisible(sig is not None and self._mate(self._signal) is not None)
        if slot is None:
            self.field_name_edit.setText("")
            self.field_byte_lbl.setText("")
            self._show_title("", "", None)
            return
        if not self.field_name_edit.hasFocus():
            self.field_name_edit.setText(slot.name)
        if self.field_type_combo.findData(slot.type) < 0:
            self.field_type_combo.addItem(slot.type, slot.type)  # unknown: shown as is
        self.field_type_combo.setCurrentIndex(self.field_type_combo.findData(slot.type))
        if self._text:
            self.field_byte_lbl.setText(f"{slot.index + 1} of {len(self.draft.fields)}")
        else:
            self.field_byte_lbl.setText(
                f"{slot.offset}  <span style='color:{TEXT_MUTED}'>"
                f"0x{slot.offset:02X} · {slot.size} B</span>"
            )

        is_time = slot.name == self.draft.time_value("field")
        self.not_plotted_lbl.setText("The X axis." if is_time else "Not plotted.")
        self.plot_btn.setVisible(not is_time)
        if sig is None:
            self.label_edit.setText("")
            self._show_title(slot.name, "", None)
            return
        color = str(sig.get("color", "#FFFFFF"))
        line = _as_dict(sig.get("line"))
        style = str(line.get("style", "solid"))
        shown = bool(sig.get("visible", True))
        self._show_title(str(sig.get("label", self._signal)), slot.name, (color, shown, style))
        if not self.label_edit.hasFocus():
            self.label_edit.setText(str(sig.get("label", "")))
        self._fill_lane_combo(str(sig.get("group", DEFAULT_LANE)))
        self.color_btn.set_color(color)
        self._check(self.style_group, LINE_STYLES.index(style) if style in LINE_STYLES else -1)
        for i, btn in enumerate(self.style_group.buttons()):
            on = i == LINE_STYLES.index(style) if style in LINE_STYLES else False
            btn.setIcon(_line_icon(color if on else "#6a6a6a", LINE_STYLES[i]))
            btn.setIconSize(QtCore.QSize(22, 12))
        width = line.get("width", 1)
        self._check(self.width_group, LINE_WIDTHS.index(width) if width in LINE_WIDTHS else -1)
        self.shown_chk.setChecked(shown)
        self.shown_chk.set_swatch(color, style != "solid")

    @staticmethod
    def _check(group: QtWidgets.QButtonGroup, index: int) -> None:
        """Checks one button of an exclusive group, or none (a value it doesn't offer)."""
        group.setExclusive(False)
        for i, btn in enumerate(group.buttons()):
            btn.setChecked(i == index)
        group.setExclusive(True)

    def _show_title(self, title: str, sub: str, swatch: tuple[str, bool, str] | None) -> None:
        color = swatch[0] if swatch is not None else TEXT
        self.title_lbl.setText(title)
        self.title_lbl.setStyleSheet(f"color: {color}; font-size: 14px; font-weight: bold;")
        self.sub_lbl.setText(sub)
        self.sub_lbl.setVisible(bool(sub))
        if swatch is None:
            self.head_swatch.clear()
            return
        self.head_swatch.setPixmap(_swatch_pixmap(swatch[0], swatch[1], swatch[2] != "solid"))

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

    def _fill_plot_menu(self) -> None:
        self.plot_menu.clear()
        for key, label in self._lanes():
            action = self.plot_menu.addAction(label)
            assert action is not None
            action.triggered.connect(lambda _=False, k=key: self.plot_in(k))
        self.plot_menu.addSeparator()
        new = self.plot_menu.addAction("New lane")
        assert new is not None
        new.triggered.connect(lambda: self.plot_in(NEW_LANE))

    # --- pairs (L=R) ---

    def _mate(self, sid: str | None) -> str | None:
        """The other side of `sid`'s pair in its lane, if it has one."""
        if sid is None:
            return None
        _, assignment = lane_layout(self.draft.data)
        lane = assignment.get(sid)
        members = [s for s, key in assignment.items() if key == lane]
        for row in pair_rows(members, self._signal_dicts()):
            if sid in row.members and row.left is not None and row.right is not None:
                return row.right if sid == row.left else row.left
        return None

    @property
    def linked(self) -> bool:
        return self.link_btn.isChecked()

    # --- selection ---

    def _field_index(self) -> int | None:
        names = self.draft.field_names()
        return names.index(self._field) if self._field in names else None

    def _select_field(self, field: str | None, signal: str | None = None) -> None:
        self._field = field
        if signal is None and field is not None:
            signal = next(iter(self.draft.signals_of_field(field)), None)
        self._signal = signal

    def _selection_key(self) -> str | None:
        if self._signal is not None:
            return self._signal
        return FIELD_MARK + self._field if self._field is not None else None

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

    def _show_selection(self) -> None:
        """A new selection: lights it everywhere without rebuilding the list."""
        self._loading = True
        try:
            self.tree.select(self._selection_key())
            self.frame_strip.set_selected(self._field_index())
            self.line_view.set_selected(self._field_index())
            self._refresh_form()
        finally:
            self._loading = False

    def _on_frame_clicked(self, index: int) -> None:
        self.flush()
        self._select_field(self.draft.field_names()[index])
        self._show_selection()

    def _on_picked(self, key: str) -> None:
        """A cell or row pressed in the list: a signal, or a field without one."""
        self.flush()
        if key.startswith(FIELD_MARK):
            self._field, self._signal = key[len(FIELD_MARK) :], None
        else:
            self._field, self._signal = str(self.draft.signal(key).get("field")), key
        self._show_selection()

    # --- edits ---

    def _edited(self) -> None:
        self.flush()  # text still being typed elsewhere isn't overwritten by the redraw
        self.refresh()
        self.changed.emit()

    def _edited_later(self) -> None:
        """Redraws on the next event-loop turn: the list's item under the mouse must outlive
        the Qt handler that reported it (a redraw frees it: the crash fixed in #32)."""
        QtCore.QTimer.singleShot(0, self._edited)

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

    def _on_endianness(self, index: int) -> None:
        if ENDIANNESS[index] != self.draft.endianness:
            self.draft.endianness = ENDIANNESS[index]
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
        self.changed.emit()  # the tab's message drops the error

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

    @staticmethod
    def _current(sig: dict[str, Any], attr: str) -> Any:
        if attr in ("style", "width"):
            line = sig.get("line")
            return line.get(attr) if isinstance(line, dict) else None
        return sig.get(attr)

    def _set_signal(self, attr: str, value: Any) -> None:
        """Sets one attribute of the selected signal, and of its pair's other side for the
        attributes L=R links (lane, colour, width)."""
        if self._loading or self._signal is None:
            return
        # The mate is found before the change: a lane change would split the pair first.
        mate = self._mate(self._signal) if self.linked and attr in LINKED else None
        targets = [s for s in (self._signal, mate) if s is not None]
        changed = False
        for sid in targets:
            if self._current(self.draft.signal(sid), attr) != value:
                self.draft.set_signal(sid, attr, value)
                changed = True
        if changed:
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

    def _on_toggled(self, sid: str) -> None:
        """A swatch pressed in the list: shown when the stream opens, or not."""
        visible = not self.draft.signal(sid).get("visible", True)
        self.draft.set_signal(sid, "visible", visible)
        self._edited_later()

    def _row_members(self, sid: str) -> list[str]:
        mate = self._mate(sid)
        return [sid] + ([mate] if mate is not None else [])

    def _on_dropped(self, sid: str, lane: str) -> None:
        """A drag onto a lane: plot a field there, move a row, or stop plotting it."""
        if sid.startswith(FIELD_MARK):
            if lane == UNPLOTTED_LANE:
                return
            field = sid[len(FIELD_MARK) :]
            lane = self.new_lane() if lane == NEW_LANE else lane
            self._field, self._signal = field, self.draft.add_signal(field, lane or None)
        elif lane == UNPLOTTED_LANE:
            field = str(self.draft.signal(sid).get("field"))
            for member in self._row_members(sid):
                self.draft.remove_signal(member)
            self._select_field(field)
        else:
            lane = self.new_lane() if lane == NEW_LANE else lane
            for member in self._row_members(sid):  # a pair moves together
                self.draft.set_signal(member, "group", lane)
            self._signal = sid
        self._edited()

    def plot_in(self, lane: str) -> None:
        """Plots the selected field in `lane` (`NEW_LANE`: a new one)."""
        if self._field is None or self._signal is not None:
            return
        lane = self.new_lane() if lane == NEW_LANE else lane
        self._signal = self.draft.add_signal(self._field, lane or None)
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
            (f"Insert {thing} after", self.add_field_after),
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
        self._field_menu().exec(global_pos)  # the press already selected the cell

    def rename_lane(self, lane: str, label: str | None = None) -> None:
        if label is None:
            current = self.draft.lane_label(lane) or lane or DEFAULT_LANE_LABEL
            label, ok = QtWidgets.QInputDialog.getText(self, "Rename lane", "Lane:", text=current)
            if not ok:
                return
        if label.strip():
            self.draft.set_lane_label(lane, label.strip())
            self._edited()
