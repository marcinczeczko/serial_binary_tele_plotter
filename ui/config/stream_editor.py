"""
Stream Editor Module.

Edits one stream definition. The editor is lossless (C4): it only overwrites the keys it
shows (name, panel type, stream ID, endianness, time base, field names and types, signal
label, field, color, visibility, line style and width, lane). Every other key in the stream,
frame, time, field, signal or line object is carried through unchanged, in its original
order. A time key is written only if it was in the file or its value differs from the
default, so an untouched stream saves byte-identically.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from PyQt6 import QtCore, QtGui, QtWidgets

# Core Imports
from core.acquisition.timebase import DEFAULT_SCALE_S
from core.config import ENDIANNESS, PANEL_TYPES
from core.protocol.constants import LOOP_CNTR_NAME, STRUCT_TYPE_MAP
from core.types import StreamConfig, StreamFrameField

# Common UI Imports
from ui.charts.telemetry_plot import DEFAULT_LINE_WIDTH
from ui.common.color_button import ColorButton

# Per-row storage of the original objects, so get_data() can round-trip unknown keys.
ROLE_ORIGINAL = QtCore.Qt.ItemDataRole.UserRole
ROLE_KEY = QtCore.Qt.ItemDataRole.UserRole + 1


class _Original:
    """
    Opaque holder for a row's original dict.

    Storing a plain dict as item data converts it to a QVariantMap, which sorts the keys.
    That breaks the byte-identical round trip.
    """

    def __init__(self, data: dict[str, Any] | None) -> None:
        self.data = data or {}


def _original_of(value: Any) -> dict[str, Any]:
    return copy.deepcopy(value.data) if isinstance(value, _Original) else {}


def _select_or_add(combo: QtWidgets.QComboBox, text: str) -> None:
    """Selects `text`, adding it first if the combo doesn't offer it (never silently swap)."""
    if combo.findText(text) < 0:
        combo.addItem(text)
    combo.setCurrentText(text)


def _number_text(value: Any) -> str:
    return repr(value) if isinstance(value, float) else str(value)


def _parse_number(text: str) -> int | float | str:
    """An int when the text is one (so `1` stays `1` in JSON), else a float, else the text."""
    text = text.strip()
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text  # kept as typed; validation reports it


def _as_widget[W: QtWidgets.QWidget](widget: QtWidgets.QWidget | None, cls: type[W]) -> W:
    """Narrows a cell/item widget returned by Qt to the type the editor placed there."""
    if not isinstance(widget, cls):
        raise TypeError(f"Expected {cls.__name__}, got {type(widget).__name__}")
    return widget


class StreamEditor(QtWidgets.QWidget):
    """
    The form for editing a single stream definition.
    Manages Frame Table and a Flat Signal List.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_stream_key: str | None = None
        self._original: dict[str, Any] = {}
        self._lane_choices: list[str] = []
        self.init_ui()
        self.apply_styles()

    def apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QTreeWidget, QTableWidget {
                background-color: #121212;
                border: 1px solid #333;
                color: #e0e0e0;
                gridline-color: #2a2a2a;
                font-size: 13px;
            }
            QHeaderView::section {
                background-color: #1a1a1a;
                color: #bbb;
                padding: 6px;
                border: none;
                border-bottom: 2px solid #333;
                border-right: 1px solid #333;
                font-weight: bold;
            }
            QTreeWidget::item, QTableWidget::item {
                padding: 4px; border-bottom: 1px solid #1a1a1a; height: 32px;
            }
            QTreeWidget::item:hover, QTableWidget::item:hover { background-color: #1f1f1f; }
            QTreeWidget::item:selected, QTableWidget::item:selected {
                background-color: #2c3e50; color: white;
            }
            QComboBox, QLineEdit {
                background-color: #121212; border: 1px solid #333; color: #4FC3F7; padding: 2px 5px;
            }
        """
        )

    def init_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # --- Metadata Section ---
        grp_info = QtWidgets.QGroupBox("Stream Metadata")
        form = QtWidgets.QFormLayout(grp_info)
        self.key_edit = QtWidgets.QLineEdit()
        self.name_edit = QtWidgets.QLineEdit()
        self.id_spin = QtWidgets.QSpinBox()
        self.id_spin.setRange(0, 255)
        self.panel_combo = QtWidgets.QComboBox()
        self.panel_combo.addItems(PANEL_TYPES)
        self.endian_combo = QtWidgets.QComboBox()
        self.endian_combo.addItems(ENDIANNESS)

        form.addRow("JSON Key:", self.key_edit)
        form.addRow("Display Name:", self.name_edit)
        form.addRow("Stream ID:", self.id_spin)
        form.addRow("Endianness:", self.endian_combo)
        form.addRow("Panel Type:", self.panel_combo)
        layout.addWidget(grp_info)

        # --- Time base (R2.5) ---
        grp_time = QtWidgets.QGroupBox("Time Base")
        time_form = QtWidgets.QFormLayout(grp_time)
        self.time_field_combo = QtWidgets.QComboBox()
        self.time_field_combo.setToolTip("Frame field that drives the X axis.")
        self.time_scale_edit = QtWidgets.QLineEdit()
        self.time_scale_edit.setToolTip(
            "Seconds per tick of the time field: the MCU loop period for a loop counter "
            "(0.005 = 5 ms), or 1e-6 for a microsecond timestamp."
        )
        self.time_step_edit = QtWidgets.QLineEdit()
        self.time_step_edit.setToolTip(
            "Nominal increase of the time field per frame: 1 for a loop counter. A larger "
            "jump is drawn as a gap (lost frames)."
        )
        time_form.addRow("Time field:", self.time_field_combo)
        time_form.addRow("Seconds per tick:", self.time_scale_edit)
        time_form.addRow("Ticks per frame:", self.time_step_edit)
        layout.addWidget(grp_time)

        # --- Tabs ---
        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs)

        self.frame_widget = QtWidgets.QWidget()
        self._init_frame_tab()
        self.tabs.addTab(self.frame_widget, "1. Binary Frame Def")

        self.sig_widget = QtWidgets.QWidget()
        self._init_signal_tab()
        self.tabs.addTab(self.sig_widget, "2. Signals (Flat List)")

    def _init_frame_tab(self) -> None:
        layout = QtWidgets.QVBoxLayout(self.frame_widget)
        self.frame_table = QtWidgets.QTableWidget()
        self.frame_table.setColumnCount(2)
        self.frame_table.setHorizontalHeaderLabels(["Field Name (C++)", "Data Type"])
        h = self.frame_table.horizontalHeader()
        assert h is not None
        h.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Fixed)
        h.resizeSection(1, 150)
        v = self.frame_table.verticalHeader()
        assert v is not None
        v.setVisible(False)

        btns = QtWidgets.QHBoxLayout()
        b_add = QtWidgets.QPushButton("+ Add")
        b_add.clicked.connect(lambda: self.add_frame_row())
        b_del = QtWidgets.QPushButton("- Del")
        b_del.clicked.connect(lambda: self.remove_table_row(self.frame_table))
        # Keep the signals' "Field Map" choices in sync with the frame's field names.
        self.frame_table.itemChanged.connect(lambda _item: self._refresh_field_combos())
        btns.addWidget(b_add)
        btns.addWidget(b_del)
        btns.addStretch()
        layout.addWidget(self.frame_table)
        layout.addLayout(btns)

    def _init_signal_tab(self) -> None:
        layout = QtWidgets.QVBoxLayout(self.sig_widget)

        self.sig_tree = QtWidgets.QTreeWidget()
        self.sig_tree.setRootIsDecorated(False)

        # Cols: Label | Field | Color | Vis | Style
        self.sig_cols = ["Label Name", "Field Map", "Color", "Vis", "Style", "Width", "Lane"]
        self.sig_tree.setColumnCount(len(self.sig_cols))
        self.sig_tree.setHeaderLabels(self.sig_cols)

        h = self.sig_tree.header()
        assert h is not None
        h.resizeSection(0, 200)
        h.resizeSection(1, 150)
        h.resizeSection(2, 80)
        h.resizeSection(3, 40)

        btns = QtWidgets.QHBoxLayout()
        b_sig = QtWidgets.QPushButton("📈 Add Signal")
        b_sig.clicked.connect(lambda: self.add_signal_item())
        b_rem = QtWidgets.QPushButton("❌ Remove")
        b_rem.clicked.connect(self.remove_tree_item)
        btns.addWidget(b_sig)
        btns.addWidget(b_rem)
        btns.addStretch()
        layout.addWidget(self.sig_tree)
        layout.addLayout(btns)

    def load_data(self, key: str, data: StreamConfig) -> None:
        self.current_stream_key = key
        self._original = copy.deepcopy(dict(data))
        frame = data.get("frame", {})

        self.key_edit.setText(key)
        self.name_edit.setText(data.get("name", ""))
        _select_or_add(self.panel_combo, data.get("panel_type", "none"))
        _select_or_add(self.endian_combo, frame.get("endianness", "little"))
        self.id_spin.setValue(frame.get("stream_id", 0))

        # Frame
        self.frame_table.blockSignals(True)
        self.frame_table.setRowCount(0)
        for f in frame.get("fields", []):
            self.add_frame_row(f.get("name", ""), f.get("type", "f32"), original=dict(f))
        self.frame_table.blockSignals(False)

        # Time base
        raw_time = data.get("time")
        time_cfg: dict[str, Any] = dict(raw_time) if isinstance(raw_time, dict) else {}
        self._refresh_time_field_combo(str(time_cfg.get("field", LOOP_CNTR_NAME)))
        self.time_scale_edit.setText(_number_text(time_cfg.get("scale_s", DEFAULT_SCALE_S)))
        self.time_step_edit.setText(_number_text(time_cfg.get("step", 1)))

        # Signals (flat list). Lane choices: the described groups, then any in use.
        groups = data.get("groups")
        lanes = list(groups) if isinstance(groups, dict) else []
        for sdata in data.get("signals", {}).values():
            group = sdata.get("group") if isinstance(sdata, dict) else None
            if isinstance(group, str) and group and group not in lanes:
                lanes.append(group)
        self._lane_choices = lanes
        self.sig_tree.clear()
        for skey, sdata in data.get("signals", {}).items():
            line = sdata.get("line", {})
            row = {
                "label": sdata.get("label", skey),
                "field": sdata.get("field", ""),
                "color": sdata.get("color", "#FFFFFF"),
                "visible": sdata.get("visible", True),
                "style": line.get("style", "solid"),
                "width": line.get("width", DEFAULT_LINE_WIDTH),
                "lane": sdata.get("group", "") if isinstance(sdata.get("group"), str) else "",
            }
            self.add_signal_row(row, key=skey, original=dict(sdata))

    def get_data(self) -> tuple[str, StreamConfig]:
        """Returns (key, stream): the loaded stream with the edited values overlaid."""
        data: dict[str, Any] = copy.deepcopy(self._original)
        data["name"] = self.name_edit.text()
        data["panel_type"] = self.panel_combo.currentText()

        frame = data["frame"] if isinstance(data.get("frame"), dict) else {}
        frame["stream_id"] = self.id_spin.value()
        frame["endianness"] = self.endian_combo.currentText()
        fields: list[StreamFrameField] = []
        for r in range(self.frame_table.rowCount()):
            name = self._frame_name(r)
            if not name:
                continue
            name_item = self.frame_table.item(r, 0)
            field = _original_of(name_item.data(ROLE_ORIGINAL) if name_item else None)
            type_combo = _as_widget(self.frame_table.cellWidget(r, 1), QtWidgets.QComboBox)
            field["name"] = name
            field["type"] = type_combo.currentData() or type_combo.currentText()
            fields.append(field)  # type: ignore[arg-type]
        frame["fields"] = fields
        data["frame"] = frame
        self._put_time(data)

        signals: dict[str, dict[str, Any]] = {}
        root = self.sig_tree.invisibleRootItem()
        assert root is not None
        for i in range(root.childCount()):
            item = root.child(i)
            assert item is not None
            sig = _original_of(item.data(0, ROLE_ORIGINAL))

            label = item.text(0)
            fld = _as_widget(self.sig_tree.itemWidget(item, 1), QtWidgets.QComboBox).currentText()
            vis_box = _as_widget(self.sig_tree.itemWidget(item, 3), QtWidgets.QWidget)
            vis = _as_widget(vis_box.findChild(QtWidgets.QCheckBox), QtWidgets.QCheckBox)
            sig["label"] = label
            sig["field"] = fld
            sig["color"] = _as_widget(self.sig_tree.itemWidget(item, 2), ColorButton).text()
            sig["visible"] = vis.isChecked()
            line = sig["line"] if isinstance(sig.get("line"), dict) else {}
            line["style"] = _as_widget(
                self.sig_tree.itemWidget(item, 4), QtWidgets.QComboBox
            ).currentText()
            line["width"] = _as_widget(
                self.sig_tree.itemWidget(item, 5), QtWidgets.QSpinBox
            ).value()
            sig["line"] = line
            lane = _as_widget(self.sig_tree.itemWidget(item, 6), QtWidgets.QComboBox)
            lane_text = lane.currentText().strip()
            if lane_text:
                sig["group"] = lane_text
            else:
                sig.pop("group", None)  # the default lane

            key = item.data(0, ROLE_KEY)
            if not isinstance(key, str) or not key or key in signals:
                key = self._unique_signal_key(fld or label, signals)
                item.setData(0, ROLE_KEY, key)
            signals[key] = sig
        data["signals"] = signals
        return self.key_edit.text(), data  # type: ignore[return-value]

    def _put_time(self, data: dict[str, Any]) -> None:
        """Writes the time base, touching only keys that were there or now differ."""
        original = data.get("time")
        time_cfg: dict[str, Any] = copy.deepcopy(original) if isinstance(original, dict) else {}
        edited: list[tuple[str, Any, Any]] = [
            ("field", self.time_field_combo.currentText(), LOOP_CNTR_NAME),
            ("scale_s", _parse_number(self.time_scale_edit.text()), DEFAULT_SCALE_S),
            ("step", _parse_number(self.time_step_edit.text()), 1),
        ]
        for key, value, default in edited:
            if key in time_cfg or value != default:
                time_cfg[key] = value
        if time_cfg or isinstance(original, dict):
            data["time"] = time_cfg

    def _refresh_time_field_combo(self, current: str | None = None) -> None:
        """Lists the frame's fields as time field choices, keeping the selection."""
        combo = self.time_field_combo
        current = combo.currentText() if current is None else current
        fields = self.get_fields()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(fields)
        if current and current not in fields:
            combo.addItem(current)  # stale choice stays visible; validation flags it
        combo.setCurrentText(current)
        combo.blockSignals(False)

    @staticmethod
    def _unique_signal_key(base: str, taken: dict[str, Any]) -> str:
        """Signal keys identify signals, so two signals on one field must not collide (C4d)."""
        stem = re.sub(r"[^a-zA-Z0-9_]", "", base) or "signal"
        key, n = stem, 2
        while key in taken:
            key, n = f"{stem}_{n}", n + 1
        return key

    # --- Helpers ---
    def add_frame_row(
        self, name: str = "", ftype: str = "f32", original: dict[str, Any] | None = None
    ) -> None:
        r = self.frame_table.rowCount()
        self.frame_table.insertRow(r)
        item = QtWidgets.QTableWidgetItem(name)
        item.setData(ROLE_ORIGINAL, _Original(original))
        self.frame_table.setItem(r, 0, item)
        combo = QtWidgets.QComboBox()
        for k, v in STRUCT_TYPE_MAP.items():
            combo.addItem(v[2], k)
        if combo.findData(ftype) < 0:
            combo.addItem(ftype, ftype)  # keep unknown types visible; validation reports them
        combo.setCurrentIndex(combo.findData(ftype))
        self.frame_table.setCellWidget(r, 1, combo)
        self._refresh_field_combos()

    def remove_table_row(self, t: QtWidgets.QTableWidget) -> None:
        if t.currentRow() >= 0:
            t.removeRow(t.currentRow())
            self._refresh_field_combos()

    def _refresh_field_combos(self) -> None:
        """Re-lists frame fields in every signal's "Field Map", keeping each selection."""
        self._refresh_time_field_combo()
        fields = self.get_fields()
        root = self.sig_tree.invisibleRootItem()
        if root is None:
            return
        for i in range(root.childCount()):
            item = root.child(i)
            combo = self.sig_tree.itemWidget(item, 1) if item is not None else None
            if not isinstance(combo, QtWidgets.QComboBox):
                continue
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(fields)
            if current and current not in fields:
                combo.addItem(current)  # stale mapping stays visible; validation flags it
            combo.setCurrentText(current)
            combo.blockSignals(False)

    def _frame_name(self, row: int) -> str:
        item = self.frame_table.item(row, 0)
        return item.text() if item is not None else ""

    def get_fields(self) -> list[str]:
        return [
            self._frame_name(r).strip()
            for r in range(self.frame_table.rowCount())
            if self._frame_name(r).strip()
        ]

    def add_signal_item(self) -> None:
        # Adds directly to root (flat list)
        self.add_signal_row()

    def add_signal_row(
        self,
        d: dict[str, Any] | None = None,
        key: str | None = None,
        original: dict[str, Any] | None = None,
    ) -> None:
        if not d:
            d = {
                "label": "New Signal",
                "field": "",
                "color": "#4FC3F7",
                "visible": True,
                "style": "solid",
                "width": DEFAULT_LINE_WIDTH,
            }

        item = QtWidgets.QTreeWidgetItem(self.sig_tree)
        item.setText(0, d["label"])
        item.setData(0, ROLE_KEY, key)
        item.setData(0, ROLE_ORIGINAL, _Original(original))
        item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsEditable)

        # Style row
        for c in range(len(self.sig_cols)):
            item.setSizeHint(c, QtCore.QSize(0, 30))
            item.setForeground(c, QtGui.QBrush(QtGui.QColor("#e0e0e0")))

        # Col 1: Field Map
        cb_fld = QtWidgets.QComboBox()
        cb_fld.addItems(self.get_fields())
        if d["field"] and d["field"] not in self.get_fields():
            cb_fld.addItem(d["field"])
        cb_fld.setCurrentText(d["field"])

        # Col 3: Visible Checkbox
        chk = QtWidgets.QCheckBox()
        chk.setChecked(d["visible"])
        w_chk = QtWidgets.QWidget()
        chk_layout = QtWidgets.QHBoxLayout(w_chk)
        chk_layout.setContentsMargins(0, 0, 0, 0)
        chk_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        chk_layout.addWidget(chk)

        # Col 4: Style
        cb_sty = QtWidgets.QComboBox()
        cb_sty.addItems(["solid", "dashed", "dotted"])
        cb_sty.setCurrentText(d["style"])

        # Col 5: Line width (1 px draws fastest; see P4)
        sb_width = QtWidgets.QSpinBox()
        sb_width.setRange(1, 5)
        sb_width.setValue(int(d["width"]))

        self.sig_tree.setItemWidget(item, 1, cb_fld)
        self.sig_tree.setItemWidget(item, 2, ColorButton(d["color"]))
        self.sig_tree.setItemWidget(item, 3, w_chk)
        self.sig_tree.setItemWidget(item, 4, cb_sty)
        self.sig_tree.setItemWidget(item, 5, sb_width)

        # Col 6: Lane (signals[*].group); empty is the default lane, a new name a new lane
        cb_lane = QtWidgets.QComboBox()
        cb_lane.setEditable(True)
        cb_lane.addItem("")
        cb_lane.addItems(self._lane_choices)
        lane = d.get("lane", "")
        if lane and cb_lane.findText(lane) < 0:
            cb_lane.addItem(lane)
        cb_lane.setCurrentText(lane)
        self.sig_tree.setItemWidget(item, 6, cb_lane)

    def remove_tree_item(self) -> None:
        # Removes selected signal
        for item in self.sig_tree.selectedItems():
            index = self.sig_tree.indexOfTopLevelItem(item)
            self.sig_tree.takeTopLevelItem(index)
