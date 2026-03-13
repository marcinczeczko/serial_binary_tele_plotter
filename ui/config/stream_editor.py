"""
Stream Editor Module.
Simplified Version: Flat signal list (no groups), removed Lock/Scale features.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any

from PyQt6 import QtCore, QtGui, QtWidgets

# Core Imports
from core.protocol.constants import STRUCT_TYPE_MAP
from core.types import StreamConfig

# Common UI Imports
from ui.common.color_button import ColorButton

PANEL_TYPES = ["none", "pid", "imu", "control"]


class StreamEditor(QtWidgets.QWidget):
    """
    The form for editing a single stream definition.
    Manages Frame Table and a Flat Signal List.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_stream_key: str | None = None
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
            QTreeWidget::item:selected, QTableWidget::item:selected { background-color: #2c3e50; color: white; }
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

        form.addRow("JSON Key:", self.key_edit)
        form.addRow("Display Name:", self.name_edit)
        form.addRow("Stream ID:", self.id_spin)
        form.addRow("Panel Type:", self.panel_combo)
        layout.addWidget(grp_info)

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
        l = QtWidgets.QVBoxLayout(self.frame_widget)
        self.frame_table = QtWidgets.QTableWidget()
        self.frame_table.setColumnCount(2)
        self.frame_table.setHorizontalHeaderLabels(["Field Name (C++)", "Data Type"])
        h = self.frame_table.horizontalHeader()
        h.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Fixed)
        h.resizeSection(1, 150)
        self.frame_table.verticalHeader().setVisible(False)

        btns = QtWidgets.QHBoxLayout()
        b_add = QtWidgets.QPushButton("+ Add")
        b_add.clicked.connect(self.add_frame_row)
        b_del = QtWidgets.QPushButton("- Del")
        b_del.clicked.connect(lambda: self.remove_table_row(self.frame_table))
        btns.addWidget(b_add)
        btns.addWidget(b_del)
        btns.addStretch()
        l.addWidget(self.frame_table)
        l.addLayout(btns)

    def _init_signal_tab(self) -> None:
        l = QtWidgets.QVBoxLayout(self.sig_widget)

        self.sig_tree = QtWidgets.QTreeWidget()
        self.sig_tree.setRootIsDecorated(False)  # Ukrywamy strzałki (bo brak grup)

        # Cols: Label | Field | Color | Vis | Style
        self.sig_cols = ["Label Name", "Field Map", "Color", "Vis", "Style"]
        self.sig_tree.setColumnCount(len(self.sig_cols))
        self.sig_tree.setHeaderLabels(self.sig_cols)

        h = self.sig_tree.header()
        h.resizeSection(0, 200)
        h.resizeSection(1, 150)
        h.resizeSection(2, 80)
        h.resizeSection(3, 40)

        btns = QtWidgets.QHBoxLayout()
        # Usunięto przycisk "Add Group"
        b_sig = QtWidgets.QPushButton("📈 Add Signal")
        b_sig.clicked.connect(self.add_signal_item)
        b_rem = QtWidgets.QPushButton("❌ Remove")
        b_rem.clicked.connect(self.remove_tree_item)
        btns.addWidget(b_sig)
        btns.addWidget(b_rem)
        btns.addStretch()
        l.addWidget(self.sig_tree)
        l.addLayout(btns)

    def load_data(self, key: str, data: StreamConfig) -> None:
        self.current_stream_key = key
        self.key_edit.setText(key)
        self.name_edit.setText(data.get("name", ""))

        idx = self.panel_combo.findText(data.get("panel_type", "none"))
        if idx >= 0:
            self.panel_combo.setCurrentIndex(idx)

        self.id_spin.setValue(data.get("frame", {}).get("stream_id", 0))

        # Frame
        self.frame_table.setRowCount(0)
        for f in data.get("frame", {}).get("fields", []):
            self.add_frame_row(f.get("name"), f.get("type"))

        # Signals (Flattened)
        self.sig_tree.clear()

        # Pobieramy sygnały. Ignorujemy klucz "groups" całkowicie.
        signals_data = data.get("signals", {})

        # Jeśli sygnały mają pole 'order', możemy posortować, w przeciwnym razie wg klucza lub kolejności JSON
        # W Python 3.7+ słowniki zachowują kolejność wstawiania, więc powinno być ok.
        for skey, sdata in signals_data.items():
            row = {
                "label": sdata.get("label", skey),
                "field": sdata.get("field", ""),
                "color": sdata.get("color", "#FFFFFF"),
                "visible": sdata.get("visible", True),
                "style": sdata.get("line", {}).get("style", "solid"),
            }
            self.add_signal_row(row)

    def get_data(self) -> tuple[str, StreamConfig]:
        data = OrderedDict()
        data["name"] = self.name_edit.text()
        data["panel_type"] = self.panel_combo.currentText()

        # Frame
        fields = []
        for r in range(self.frame_table.rowCount()):
            name = self.frame_table.item(r, 0).text()
            if name:
                fields.append(
                    {"name": name, "type": self.frame_table.cellWidget(r, 1).currentData()}
                )
        data["frame"] = {
            "stream_id": self.id_spin.value(),
            "endianness": "little",
            "packed": True,
            "fields": fields,
        }

        # Signals (Flat Structure)
        signals = {}
        root = self.sig_tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)

            label = item.text(0)
            fld = self.sig_tree.itemWidget(item, 1).currentText()
            col = self.sig_tree.itemWidget(item, 2).text()
            vis = self.sig_tree.itemWidget(item, 3).findChild(QtWidgets.QCheckBox).isChecked()
            style = self.sig_tree.itemWidget(item, 4).currentText()

            # Klucz sygnału: jeśli pole mapowania jest puste, generujemy z etykiety
            skey = fld if fld else re.sub(r"[^a-zA-Z0-9]", "", label)

            signals[skey] = {
                "label": label,
                "field": fld,
                "color": col,
                "visible": vis,
                "line": {"style": style, "width": 2},
                # Brak grup, brak min/max, brak lock
            }

        # Nie zapisujemy już sekcji "groups"
        data["signals"] = signals
        return self.key_edit.text(), data

    # --- Helpers ---
    def add_frame_row(self, name: str = "", ftype: str = "f32") -> None:
        r = self.frame_table.rowCount()
        self.frame_table.insertRow(r)
        self.frame_table.setItem(r, 0, QtWidgets.QTableWidgetItem(name))
        combo = QtWidgets.QComboBox()
        for k, v in STRUCT_TYPE_MAP.items():
            combo.addItem(v[2], k)
        combo.setCurrentIndex(combo.findData(ftype) if combo.findData(ftype) >= 0 else 0)
        self.frame_table.setCellWidget(r, 1, combo)

    def remove_table_row(self, t: QtWidgets.QTableWidget) -> None:
        if t.currentRow() >= 0:
            t.removeRow(t.currentRow())

    def get_fields(self) -> list[str]:
        return [
            self.frame_table.item(r, 0).text().strip()
            for r in range(self.frame_table.rowCount())
            if self.frame_table.item(r, 0).text().strip()
        ]

    def add_signal_item(self) -> None:
        # Adds directly to root (flat list)
        self.add_signal_row()

    def add_signal_row(self, d: dict[str, Any] | None = None) -> None:
        if not d:
            d = {
                "label": "New Signal",
                "field": "",
                "color": "#4FC3F7",
                "visible": True,
                "style": "solid",
            }

        item = QtWidgets.QTreeWidgetItem(self.sig_tree)
        item.setText(0, d["label"])
        item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsEditable)

        # Style row
        for c in range(5):
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
        l = QtWidgets.QHBoxLayout(w_chk)
        l.setContentsMargins(0, 0, 0, 0)
        l.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        l.addWidget(chk)

        # Col 4: Style
        cb_sty = QtWidgets.QComboBox()
        cb_sty.addItems(["solid", "dashed", "dotted"])
        cb_sty.setCurrentText(d["style"])

        self.sig_tree.setItemWidget(item, 1, cb_fld)
        self.sig_tree.setItemWidget(item, 2, ColorButton(d["color"]))
        self.sig_tree.setItemWidget(item, 3, w_chk)
        self.sig_tree.setItemWidget(item, 4, cb_sty)

    def remove_tree_item(self) -> None:
        # Removes selected signal
        for item in self.sig_tree.selectedItems():
            index = self.sig_tree.indexOfTopLevelItem(item)
            self.sig_tree.takeTopLevelItem(index)
