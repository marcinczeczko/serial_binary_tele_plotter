"""
New profile (R8.2): a device profile's name, wire format and baud, empty or a copy of the
profile in use. The file goes into the profiles folder; the main window writes it, switches
to it and opens the editor.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6 import QtWidgets

from core.config.profile import FORMAT_LABELS, new_profile_document, profile_filename
from core.protocol.link import BINARY, LINK_FORMATS
from ui.panels.connection import BAUD_RATES


class ProfileDialog(QtWidgets.QDialog):
    def __init__(
        self,
        folder: Path,
        current_name: str,
        current_doc: dict[str, Any],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("New profile")
        self.setMinimumWidth(520)
        self._folder = folder
        self._current_doc = current_doc

        form = QtWidgets.QFormLayout()
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText("e.g. robot-1")
        form.addRow("Name:", self.name_edit)

        formats = QtWidgets.QVBoxLayout()
        self.format_group = QtWidgets.QButtonGroup(self)
        self.format_buttons: dict[str, QtWidgets.QRadioButton] = {}
        for fmt in LINK_FORMATS:
            button = QtWidgets.QRadioButton(FORMAT_LABELS.get(fmt, fmt))
            self.format_group.addButton(button)
            self.format_buttons[fmt] = button
            formats.addWidget(button)
        self.format_buttons[BINARY].setChecked(True)
        form.addRow("Format:", formats)

        self.baud_combo = QtWidgets.QComboBox()
        self.baud_combo.setEditable(True)
        self.baud_combo.addItems(BAUD_RATES)
        self.baud_combo.setCurrentText("115200")
        form.addRow("Baud:", self.baud_combo)

        self.start_combo = QtWidgets.QComboBox()
        self.start_combo.addItem(f"A copy of {current_name}", "copy")
        self.start_combo.addItem("Empty", "empty")
        form.addRow("Start from:", self.start_combo)

        self.path_lbl = QtWidgets.QLabel("")
        self.path_lbl.setStyleSheet("color: #888; font-family: monospace;")
        self.path_lbl.setWordWrap(True)
        form.addRow("Saved as:", self.path_lbl)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel
            | QtWidgets.QDialogButtonBox.StandardButton.Ok
        )
        ok_btn = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        assert ok_btn is not None
        self.ok_btn: QtWidgets.QPushButton = ok_btn
        self.ok_btn.setText("Create and edit")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self.name_edit.textChanged.connect(self._update)
        self.baud_combo.currentTextChanged.connect(self._update)
        self._update()

    def fmt(self) -> str:
        return next((f for f, b in self.format_buttons.items() if b.isChecked()), BINARY)

    def baud(self) -> int | None:
        text = self.baud_combo.currentText().strip()
        return int(text) if text.isdigit() and int(text) > 0 else None

    def path(self) -> Path:
        return profile_filename(self.name_edit.text() or "profile", self._folder)

    def document(self) -> dict[str, Any]:
        copy_of = self._current_doc if self.start_combo.currentData() == "copy" else None
        return new_profile_document(self.name_edit.text().strip(), self.fmt(), self.baud(), copy_of)

    def _update(self) -> None:
        self.path_lbl.setText(str(self.path()))
        self.ok_btn.setEnabled(bool(self.name_edit.text().strip()) and self.baud() is not None)
