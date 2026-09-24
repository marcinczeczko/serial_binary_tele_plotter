"""
Configuration Tab Module.
Manages the list of streams, file I/O operations, and integrates the Stream Editor.

Edits are committed to the in-memory document whenever the selection moves to another
stream, so nothing is lost before saving (C4a). Saving validates the whole document first
and refuses to write a file the app couldn't load.
"""

from __future__ import annotations

import json
import logging
import shutil

from PyQt6 import QtCore, QtWidgets

from core.config import validate_config
from core.types import StreamConfig
from ui.config.stream_editor import StreamEditor

logger = logging.getLogger(__name__)


class ConfiguratorTab(QtWidgets.QWidget):
    """
    Main Configuration Tab.
    Left: List of defined streams.
    Right: StreamEditor for the selected stream.
    """

    config_saved = QtCore.pyqtSignal()

    def __init__(
        self, filepath: str = "streams.json", parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.filepath: str = filepath
        self.data: dict[str, StreamConfig] = {}
        self.init_ui()
        self.load_from_file()

    def init_ui(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # --- Left Panel: Stream List ---
        left_panel = QtWidgets.QWidget()
        l_left = QtWidgets.QVBoxLayout(left_panel)
        l_left.setContentsMargins(0, 0, 0, 0)

        self.stream_list = QtWidgets.QListWidget()
        self.stream_list.setStyleSheet(
            """
            QListWidget { background-color: #121212; border: 1px solid #333; font-size: 13px; }
            QListWidget::item { padding: 8px; border-bottom: 1px solid #1a1a1a; }
            QListWidget::item:selected {
                background-color: #2c3e50; color: white; border-left: 3px solid #4FC3F7;
            }
        """
        )
        self.stream_list.currentItemChanged.connect(self._on_current_item_changed)

        # Action Buttons
        hbox = QtWidgets.QHBoxLayout()
        b_new = QtWidgets.QPushButton("New")
        b_new.clicked.connect(self.create_stream)
        b_del = QtWidgets.QPushButton("Delete")
        b_del.clicked.connect(self.delete_stream)
        hbox.addWidget(b_new)
        hbox.addWidget(b_del)

        b_save = QtWidgets.QPushButton("💾 SAVE TO DISK")
        b_save.setStyleSheet(
            "QPushButton { background-color: #2E7D32; color: white;"
            " font-weight: bold; padding: 10px; }"
            " QPushButton:hover { background-color: #388E3C; }"
        )
        b_save.clicked.connect(self.save_to_file)

        l_left.addWidget(QtWidgets.QLabel("Available Streams:"))
        l_left.addWidget(self.stream_list)
        l_left.addLayout(hbox)
        l_left.addWidget(b_save)

        # --- Right Panel: Editor ---
        self.editor = StreamEditor()

        # Splitter
        splitter = QtWidgets.QSplitter()
        splitter.addWidget(left_panel)
        splitter.addWidget(self.editor)
        splitter.setSizes([250, 800])
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background-color: #333; }")

        layout.addWidget(splitter)

    def load_from_file(self) -> None:
        try:
            with open(self.filepath, encoding="utf-8") as f:
                self.data = json.load(f).get("streams", {})
        except (OSError, json.JSONDecodeError) as e:
            logger.exception("Config load error: %s", e)
            self.data = {}
        self.refresh_list()

    def refresh_list(self) -> None:
        self.stream_list.blockSignals(True)
        self.stream_list.clear()
        self.editor.current_stream_key = None
        for k in self.data.keys():
            self.stream_list.addItem(k)
        self.stream_list.blockSignals(False)
        if self.stream_list.count() > 0:
            self.stream_list.setCurrentRow(0)  # emits currentItemChanged -> loads it

    def _on_current_item_changed(
        self, current: QtWidgets.QListWidgetItem | None, previous: QtWidgets.QListWidgetItem | None
    ) -> None:
        if previous is not None:
            self._commit(previous)
        self._load_item(current)

    def _load_item(self, item: QtWidgets.QListWidgetItem | None) -> None:
        if item is not None and item.text() in self.data:
            self.editor.load_data(item.text(), self.data[item.text()])
        else:
            self.editor.current_stream_key = None

    def _commit(self, item: QtWidgets.QListWidgetItem) -> None:
        """Writes the editor's content back into `self.data` for the stream shown by `item`."""
        old_k = item.text()
        if self.editor.current_stream_key != old_k or old_k not in self.data:
            return  # nothing loaded for this item (e.g. it was just deleted)
        new_k, content = self.editor.get_data()
        new_k = new_k.strip() or old_k
        if new_k != old_k and new_k in self.data:
            QtWidgets.QMessageBox.warning(
                self, "Duplicate key", f"A stream named '{new_k}' already exists; kept '{old_k}'."
            )
            new_k = old_k
        # Rebuild to keep the stream at its position when renamed.
        self.data = {(new_k if k == old_k else k): v for k, v in self.data.items()}
        self.data[new_k] = content
        item.setText(new_k)
        self.editor.current_stream_key = new_k

    def create_stream(self) -> None:
        i = 1
        while f"new_stream_{i}" in self.data:
            i += 1
        key = f"new_stream_{i}"

        self.data[key] = {
            "name": "New Stream",
            "panel_type": "none",
            "frame": {
                "stream_id": 0,
                "endianness": "little",
                "fields": [{"name": "loop_cntr", "type": "u32"}],
            },
            "signals": {},
        }
        self.stream_list.addItem(key)
        self.stream_list.setCurrentRow(self.stream_list.count() - 1)

    def delete_stream(self) -> None:
        r = self.stream_list.currentRow()
        item = self.stream_list.item(r)
        if item is None:
            return
        del self.data[item.text()]  # removed first, so the selection change won't re-commit it
        self.editor.current_stream_key = None
        self.stream_list.takeItem(r)

    def save_current(self) -> None:
        current = self.stream_list.currentItem()
        if current is not None:
            self._commit(current)

    def save_to_file(self) -> None:
        self.save_current()
        document = {"streams": self.data}
        problems = validate_config(document)
        errors = [str(p) for p in problems if p.severity == "error"]
        if errors:
            shown = "\n".join(errors[:15]) + ("\n..." if len(errors) > 15 else "")
            QtWidgets.QMessageBox.critical(
                self, "Not saved", f"Fix these problems before saving:\n\n{shown}"
            )
            return
        try:
            shutil.copy(self.filepath, self.filepath + ".bak")
        except OSError:
            logger.warning("Could not create backup file: %s.bak", self.filepath)
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(document, f, indent=4)
            warnings = [str(p) for p in problems]
            note = ("\n\nWarnings:\n" + "\n".join(warnings)) if warnings else ""
            QtWidgets.QMessageBox.information(self, "Saved", "Configuration saved!" + note)
            self.config_saved.emit()
        except OSError as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Save failed: {e}")
