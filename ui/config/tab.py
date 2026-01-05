"""
Configuration Tab Module.
Manages the list of streams, file I/O operations, and integrates the Stream Editor.
"""

import json
import shutil

from PyQt6 import QtCore, QtWidgets

from ui.config.stream_editor import StreamEditor


class ConfiguratorTab(QtWidgets.QWidget):
    """
    Main Configuration Tab.
    Left: List of defined streams.
    Right: StreamEditor for the selected stream.
    """

    config_saved = QtCore.pyqtSignal()

    def __init__(self, filepath="streams.json", parent=None):
        super().__init__(parent)
        self.filepath = filepath
        self.data = {}
        self.init_ui()
        self.load_from_file()

    def init_ui(self):
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
            QListWidget::item:selected { background-color: #2c3e50; color: white; border-left: 3px solid #4FC3F7; }
        """
        )
        self.stream_list.currentRowChanged.connect(self.on_stream_selected)

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
            "QPushButton { background-color: #2E7D32; color: white; font-weight: bold; padding: 10px; } QPushButton:hover { background-color: #388E3C; }"
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

    def load_from_file(self):
        try:
            with open(self.filepath, "r") as f:
                self.data = json.load(f).get("streams", {})
        except Exception as e:
            print(f"Config load error: {e}")
            self.data = {}
        self.refresh_list()

    def refresh_list(self):
        self.stream_list.clear()
        for k in self.data.keys():
            self.stream_list.addItem(k)
        if self.stream_list.count() > 0:
            self.stream_list.setCurrentRow(0)

    def on_stream_selected(self, row):
        if row < 0:
            return
        key = self.stream_list.item(row).text()
        if key in self.data:
            self.editor.load_data(key, self.data[key])

    def create_stream(self):
        i = 1
        while f"new_stream_{i}" in self.data:
            i += 1
        key = f"new_stream_{i}"

        # Uproszczona struktura nowego strumienia (brak grup)
        self.data[key] = {
            "name": "New Stream",
            "panel_type": "none",
            "frame": {"stream_id": 0, "fields": []},
            "signals": {},
        }
        self.stream_list.addItem(key)
        self.stream_list.setCurrentRow(self.stream_list.count() - 1)

    def delete_stream(self):
        r = self.stream_list.currentRow()
        if r < 0:
            return
        del self.data[self.stream_list.item(r).text()]
        self.stream_list.takeItem(r)

    def save_current(self):
        if self.stream_list.currentRow() < 0:
            return
        old_k = self.stream_list.currentItem().text()
        new_k, content = self.editor.get_data()
        if old_k != new_k:
            if old_k in self.data:
                del self.data[old_k]
            self.stream_list.currentItem().setText(new_k)
        self.data[new_k] = content

    def save_to_file(self):
        self.save_current()
        try:
            shutil.copy(self.filepath, self.filepath + ".bak")
        except:
            pass
        try:
            with open(self.filepath, "w") as f:
                json.dump({"streams": self.data}, f, indent=4)
            QtWidgets.QMessageBox.information(self, "Saved", "Configuration saved!")
            self.config_saved.emit()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Save failed: {e}")
