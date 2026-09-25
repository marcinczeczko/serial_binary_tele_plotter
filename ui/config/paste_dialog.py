"""
From C struct… (R7.2): paste the struct the firmware sends, see its frame, create a stream.

The frame is redrawn as you type, so a wrong type or a missing `packed` shows before
anything is created. "Into" makes a new stream, or replaces the fields of the stream
being edited when the firmware struct changed (settings of fields that stay are kept).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6 import QtCore, QtWidgets

from core.config import ENDIANNESS
from core.config.cstruct import ParsedStruct, parse_c_struct, replace_fields, stream_from_struct
from core.config.draft import StreamDraft, unique_name
from styles import mono_font
from ui.config.frame_view import FrameView
from ui.config.stream_editor import HEADER_STYLE, HexSpinBox, field_colors

NEW = "new"
REPLACE = "replace"


class PasteStructDialog(QtWidgets.QDialog):
    def __init__(
        self,
        taken_keys: list[str],
        taken_ids: list[int],
        current: tuple[str, dict[str, Any]] | None = None,
        scale_s: float | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Stream from a C struct")
        self.resize(1000, 640)
        self._taken_keys = set(taken_keys)
        self._taken_ids = set(taken_ids)
        self._current = current
        self._scale_s = scale_s
        self._touched: set[str] = set()  # fields the user typed in: not overwritten
        self._armed = False  # False while _reparse sets the fields itself
        self.parsed = ParsedStruct()

        body = QtWidgets.QHBoxLayout()
        left = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel("Paste the struct")
        title.setStyleSheet("color: #aaa; font-weight: bold;")
        left.addWidget(title)
        self.source_edit = QtWidgets.QPlainTextEdit()
        self.source_edit.setFont(mono_font())
        self.source_edit.setPlaceholderText(
            "#define TELEM_ID 0x04\n\ntypedef struct __attribute__((packed)) {\n"
            "    uint32_t loop_cntr;\n    float    x, y;\n} frame_t;"
        )
        self.source_edit.setMinimumWidth(380)
        left.addWidget(self.source_edit, 1)
        open_btn = QtWidgets.QPushButton("Open .h file…")
        open_btn.clicked.connect(self._open_file)
        left.addWidget(open_btn, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        body.addLayout(left)

        right = QtWidgets.QVBoxLayout()
        grid = QtWidgets.QGridLayout()
        self.into_combo = QtWidgets.QComboBox()
        self.into_combo.addItem("New stream", NEW)
        if current is not None:
            self.into_combo.addItem(
                f"Replace fields of {current[1].get('name', current[0])}", REPLACE
            )
        self.key_edit = QtWidgets.QLineEdit()
        self.name_edit = QtWidgets.QLineEdit()
        self.id_spin = HexSpinBox()
        self.endian_combo = QtWidgets.QComboBox()
        self.endian_combo.addItems(ENDIANNESS)
        grid.addWidget(QtWidgets.QLabel("Into:"), 0, 0)
        grid.addWidget(self.into_combo, 0, 1, 1, 3)
        grid.addWidget(QtWidgets.QLabel("Key:"), 1, 0)
        grid.addWidget(self.key_edit, 1, 1)
        grid.addWidget(QtWidgets.QLabel("ID:"), 1, 2)
        grid.addWidget(self.id_spin, 1, 3)
        grid.addWidget(QtWidgets.QLabel("Name:"), 2, 0)
        grid.addWidget(self.name_edit, 2, 1)
        grid.addWidget(QtWidgets.QLabel("Byte order:"), 2, 2)
        grid.addWidget(self.endian_combo, 2, 3)
        grid.setColumnStretch(1, 1)
        right.addLayout(grid)

        head = QtWidgets.QHBoxLayout()
        frame_title = QtWidgets.QLabel("Frame")
        frame_title.setStyleSheet("color: #aaa; font-weight: bold;")
        self.size_lbl = QtWidgets.QLabel("")
        self.size_lbl.setStyleSheet("color: #888;")
        head.addWidget(frame_title)
        head.addStretch()
        head.addWidget(self.size_lbl)
        right.addLayout(head)
        self.frame_view = FrameView()
        right.addWidget(self.frame_view)
        self.fields_tree = QtWidgets.QTreeWidget()
        self.fields_tree.setHeaderLabels(["Field", "Type", "Byte"])
        self.fields_tree.setRootIsDecorated(False)
        self.fields_tree.setStyleSheet("QTreeWidget { border: none; }" + HEADER_STYLE)
        right.addWidget(self.fields_tree, 1)
        self.problems_lbl = QtWidgets.QLabel("")
        self.problems_lbl.setWordWrap(True)
        self.problems_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)
        right.addWidget(self.problems_lbl)
        body.addLayout(right, 1)

        foot = QtWidgets.QHBoxLayout()
        self.summary_lbl = QtWidgets.QLabel("")
        self.summary_lbl.setStyleSheet("color: #888;")
        foot.addWidget(self.summary_lbl, 1)
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.ok_btn = QtWidgets.QPushButton("Create stream")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self.accept)
        foot.addWidget(cancel)
        foot.addWidget(self.ok_btn)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addLayout(foot)

        self.source_edit.textChanged.connect(self._reparse)
        self.into_combo.currentIndexChanged.connect(self._reparse)
        self.key_edit.textEdited.connect(lambda _t: self._touched.add("key"))
        self.name_edit.textEdited.connect(lambda _t: self._touched.add("name"))
        self.id_spin.valueChanged.connect(self._on_id_changed)
        self._reparse()

    def _on_id_changed(self, _value: int) -> None:
        if self._armed:
            self._touched.add("id")

    @property
    def mode(self) -> str:
        return str(self.into_combo.currentData())

    def set_source(self, text: str) -> None:
        self.source_edit.setPlainText(text)

    def _open_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open a C header", "", "C/C++ headers (*.h *.hpp *.c *.cpp);;All files (*)"
        )
        if path:
            try:
                self.set_source(Path(path).read_text(encoding="utf-8", errors="replace"))
            except OSError as e:
                QtWidgets.QMessageBox.warning(self, "Not read", str(e))

    def _reparse(self) -> None:
        self._armed = False
        self.parsed = parse_c_struct(self.source_edit.toPlainText())
        parsed = self.parsed
        replacing = self.mode == REPLACE
        for w in (self.key_edit, self.name_edit, self.id_spin, self.endian_combo):
            w.setEnabled(not replacing)
        if "name" not in self._touched:
            self.name_edit.setText(parsed.name or "New stream")
        if "key" not in self._touched:
            self.key_edit.setText(unique_name(parsed.name or "stream", self._taken_keys))
        if "id" not in self._touched:
            free = next((i for i in range(1, 256) if i not in self._taken_ids), 0)
            self.id_spin.setValue(parsed.stream_id if parsed.stream_id is not None else free)
        self._armed = True

        stream, summary = self.result_stream()
        draft = StreamDraft(stream)
        slots = draft.layout()
        self.frame_view.set_frame(slots, field_colors(draft))
        size = sum(s.size for s in slots)
        self.size_lbl.setText(f"{size} / 255 B" + (" · packed" if parsed.packed else ""))
        self.fields_tree.clear()
        for slot in slots:
            self.fields_tree.addTopLevelItem(
                QtWidgets.QTreeWidgetItem([slot.name, slot.type, str(slot.offset)])
            )
        lines = [f'<span style="color:#FF4040">{_html(p)}</span>' for p in parsed.problems]
        lines += [f'<span style="color:#FFB000">{_html(n)}</span>' for n in parsed.notes]
        self.problems_lbl.setText("<br>".join(lines))
        self.problems_lbl.setVisible(bool(lines))
        n = len(parsed.fields)
        if not n:
            self.summary_lbl.setText("Paste a struct, or just its member lines.")
        elif replacing:
            self.summary_lbl.setText(f"{n} fields read: {summary}.")
        else:
            self.summary_lbl.setText(f"{n} fields read. Labels start as the field names.")
        self.ok_btn.setText("Replace fields" if replacing else "Create stream")
        self.ok_btn.setEnabled(bool(n))

    def result_stream(self) -> tuple[dict[str, Any], str]:
        """(the stream to add or put in place, a summary of a replacement)."""
        if self.mode == REPLACE and self._current is not None:
            return replace_fields(self._current[1], self.parsed)
        stream = stream_from_struct(
            self.parsed,
            self.name_edit.text().strip() or "New stream",
            self.id_spin.value(),
            self.endian_combo.currentText(),
            self._scale_s,
        )
        return stream, ""

    def result_key(self) -> str:
        if self.mode == REPLACE and self._current is not None:
            return self._current[0]
        return unique_name(self.key_edit.text().strip() or "stream", self._taken_keys)


def _html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
