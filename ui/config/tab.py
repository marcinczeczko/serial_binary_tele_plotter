"""
The configuration editor window's content (R7.1): streams.json, laid out like the scope.

- A toolbar: New stream, From C struct…, Copy as C struct, Delete, and Revert and Save
  (orange while there are unsaved changes, Ctrl+S).
- The streams as tabs, as on the dashboard, and the `StreamEditor` for the shown one.
- A status line: the stream's size and its first problem, as validation sees it now.

Each stream is a `StreamDraft`, kept until saved or reverted, so switching streams never
loses an edit (C4a). Saving writes the whole document (schema version, commands and
panels as loaded) through `save_document`, which refuses a file the app couldn't load.
A file with an older schema is saved migrated (R5.1).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from PyQt6 import QtCore, QtGui, QtWidgets

from core.config import InvalidConfigError, StreamConfigLoader, save_document, validate_stream
from core.config.cstruct import to_c_struct
from core.config.draft import StreamDraft, unique_name
from core.protocol.constants import LOOP_CNTR_NAME
from ui.config.paste_dialog import PasteStructDialog
from ui.config.stream_editor import StreamEditor

logger = logging.getLogger(__name__)

SAVE_DIRTY = (
    "QPushButton { background-color: #f08c00; border: 1px solid #f0a030;"
    " color: white; font-weight: bold; }"
    " QPushButton:hover { background-color: #ff9d1a; }"
)


class ConfiguratorTab(QtWidgets.QWidget):
    config_saved = QtCore.pyqtSignal()

    def __init__(
        self, stream_loader: StreamConfigLoader, parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.loader = stream_loader
        self.filepath: str = str(stream_loader.path)
        self.drafts: dict[str, StreamDraft] = {}
        self._saved: str = ""  # the document as loaded or saved, to tell unsaved changes
        self._build()
        self._take_document()

    # --- construction ---

    def _build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(6, 6, 6, 6)
        bar.setSpacing(6)
        self.new_btn = QtWidgets.QPushButton("New stream")
        self.paste_btn = QtWidgets.QPushButton("From C struct…")
        self.copy_btn = QtWidgets.QPushButton("Copy as C struct")
        self.delete_btn = QtWidgets.QPushButton("Delete stream")
        self.dirty_lbl = QtWidgets.QLabel("")
        self.dirty_lbl.setStyleSheet("color: #f0a030; font-weight: bold;")
        self.revert_btn = QtWidgets.QPushButton("Revert")
        self.save_btn = QtWidgets.QPushButton("Save")
        self.save_btn.setToolTip("Save streams.json (Ctrl+S)")
        for w in (self.new_btn, self.paste_btn, self.copy_btn, self.delete_btn):
            bar.addWidget(w)
        bar.addStretch()
        bar.addWidget(self.dirty_lbl)
        bar.addWidget(self.revert_btn)
        bar.addWidget(self.save_btn)
        layout.addLayout(bar)
        line = QtWidgets.QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #333;")
        layout.addWidget(line)

        self.stream_tabs = QtWidgets.QTabBar()
        self.stream_tabs.setExpanding(False)
        self.stream_tabs.setDrawBase(False)
        tabs_row = QtWidgets.QHBoxLayout()
        tabs_row.setContentsMargins(6, 4, 6, 0)
        tabs_row.addWidget(self.stream_tabs)
        tabs_row.addStretch()
        layout.addLayout(tabs_row)
        line2 = QtWidgets.QFrame()
        line2.setFixedHeight(1)
        line2.setStyleSheet("background: #333;")
        layout.addWidget(line2)

        self.editor = StreamEditor()
        layout.addWidget(self.editor, 1)

        self.status_lbl = QtWidgets.QLabel("")
        self.status_lbl.setContentsMargins(6, 3, 6, 3)
        self.status_lbl.setStyleSheet("color: #888;")
        layout.addWidget(self.status_lbl)

        self.new_btn.clicked.connect(self.create_stream)
        self.paste_btn.clicked.connect(self.paste_struct)
        self.copy_btn.clicked.connect(self.copy_struct)
        self.delete_btn.clicked.connect(self.delete_stream)
        self.revert_btn.clicked.connect(self.revert)
        self.save_btn.clicked.connect(self.save_to_file)
        self.stream_tabs.currentChanged.connect(self._on_tab_changed)
        self.editor.changed.connect(self._on_changed)
        self.editor.key_rename_requested.connect(self.rename_stream)
        self.editor.problem.connect(self._show_problem)
        save = QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Save, self)
        save.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        save.activated.connect(self.save_to_file)

    # --- document ---

    def load_from_file(self) -> None:
        """Re-reads the file through the shared loader and shows its streams."""
        try:
            self.loader.load()
        except ValueError as e:
            logger.error("Config load error: %s", e)
        self._take_document()

    def _take_document(self) -> None:
        # Edit a private copy of the raw document, including streams with errors, so they
        # can be fixed here.
        streams = self.loader.data.get("streams", {})
        self.drafts = {str(k): StreamDraft(v) for k, v in streams.items()}
        panels = self.loader.data.get("panels")
        self.editor.set_panel_choices(list(panels) if isinstance(panels, dict) else [])
        self._saved = self._fingerprint()
        shown = self.current_key()
        self._rebuild_tabs(shown if shown in self.drafts else None)
        self._on_changed()

    def document(self) -> dict[str, Any]:
        """The document to save: as loaded, with the edited streams."""
        return {**self.loader.data, "streams": {k: d.to_stream() for k, d in self.drafts.items()}}

    def _fingerprint(self) -> str:
        return json.dumps(self.document())

    def is_dirty(self) -> bool:
        return self._fingerprint() != self._saved

    # --- tabs ---

    @staticmethod
    def _tab_text(draft: StreamDraft) -> str:
        return f"{draft.name or '(unnamed)'} · 0x{draft.stream_id:02X}"

    def _rebuild_tabs(self, select: str | None = None) -> None:
        tabs = self.stream_tabs
        tabs.blockSignals(True)
        while tabs.count():
            tabs.removeTab(0)
        for key, draft in self.drafts.items():
            tabs.setTabData(tabs.addTab(self._tab_text(draft)), key)
        tabs.blockSignals(False)
        keys = list(self.drafts)
        index = keys.index(select) if select in keys else 0
        if keys:
            tabs.setCurrentIndex(index)
            self._show(keys[index])
        else:
            self.editor.clear()

    def current_key(self) -> str | None:
        key = self.stream_tabs.tabData(self.stream_tabs.currentIndex())
        return key if isinstance(key, str) else None

    def select_stream(self, key: str) -> None:
        for i in range(self.stream_tabs.count()):
            if self.stream_tabs.tabData(i) == key:
                self.stream_tabs.setCurrentIndex(i)

    def _on_tab_changed(self, index: int) -> None:
        key = self.stream_tabs.tabData(index)
        if isinstance(key, str):
            self.editor.flush()
            self._show(key)

    def _show(self, key: str) -> None:
        self.editor.load(key, self.drafts[key])
        self._update_status()

    # --- edits ---

    def _on_changed(self) -> None:
        key = self.current_key()
        for i in range(self.stream_tabs.count()):  # a flush on a switch edits the one left
            draft = self.drafts.get(self.stream_tabs.tabData(i))
            if draft is not None:
                self.stream_tabs.setTabText(i, self._tab_text(draft))
        dirty = self.is_dirty()
        self.dirty_lbl.setText("Unsaved changes" if dirty else "")
        self.save_btn.setStyleSheet(SAVE_DIRTY if dirty else "")
        self.revert_btn.setEnabled(dirty)
        has_stream = key is not None
        for w in (self.copy_btn, self.delete_btn):
            w.setEnabled(has_stream)
        self._update_status()

    def _update_status(self) -> None:
        key = self.current_key()
        if key is None or key not in self.drafts:
            self.status_lbl.setText("No streams")
            self.status_lbl.setToolTip("")
            return
        draft = self.drafts[key]
        text = (
            f"{key} · {len(draft.fields)} fields · {len(draft.signals)} signals"
            f" · {draft.payload_size()} B"
        )
        problems = validate_stream(key, draft.to_stream())
        errors = [p for p in problems if p.severity == "error"]
        shown = errors or problems
        if shown:
            first = shown[0].message
            more = f" (+{len(shown) - 1} more)" if len(shown) > 1 else ""
            color = "#ff6b6b" if errors else "#f0a030"
            self.status_lbl.setText(f'{text} · <span style="color:{color}">{first}{more}</span>')
            self.status_lbl.setToolTip("\n".join(str(p) for p in problems))
        else:
            self.status_lbl.setText(text)
            self.status_lbl.setToolTip("")

    def _show_problem(self, message: str) -> None:
        self.status_lbl.setText(f'<span style="color:#ff6b6b">{message}</span>')

    def rename_stream(self, new_key: str) -> None:
        old = self.current_key()
        if old is None or new_key == old:
            return
        if new_key in self.drafts:
            QtWidgets.QMessageBox.warning(
                self, "Duplicate key", f"A stream named '{new_key}' already exists; kept '{old}'."
            )
            self.editor.key_edit.setText(old)
            return
        # Rebuilt to keep the stream at its position.
        self.drafts = {(new_key if k == old else k): d for k, d in self.drafts.items()}
        self._rebuild_tabs(new_key)
        self._on_changed()

    def _add_stream(self, key: str, stream: dict[str, Any]) -> None:
        self.editor.flush()
        self.drafts[key] = StreamDraft(stream)
        self._rebuild_tabs(key)
        self._on_changed()

    def create_stream(self) -> None:
        key = unique_name("new_stream", self.drafts)
        taken = {d.stream_id for d in self.drafts.values()}
        stream_id = next((i for i in range(1, 256) if i not in taken), 0)
        self._add_stream(
            key,
            {
                "name": "New stream",
                "frame": {
                    "stream_id": stream_id,
                    "endianness": "little",
                    "fields": [{"name": LOOP_CNTR_NAME, "type": "u32"}],
                },
                "signals": {},
            },
        )

    def delete_stream(self) -> None:
        key = self.current_key()
        if key is None:
            return
        index = self.stream_tabs.currentIndex()
        del self.drafts[key]
        keys = list(self.drafts)
        self._rebuild_tabs(keys[min(index, len(keys) - 1)] if keys else None)
        self._on_changed()

    def revert(self) -> None:
        """Back to the document as last loaded or saved."""
        self._take_document()

    # --- C structs ---

    def paste_dialog(self) -> PasteStructDialog:
        key = self.current_key()
        current = (key, self.drafts[key].to_stream()) if key is not None else None
        scale = self.drafts[key].time_value("scale_s") if key is not None else None
        return PasteStructDialog(
            list(self.drafts),
            [d.stream_id for d in self.drafts.values()],
            current,
            scale if isinstance(scale, int | float) else None,
            self,
        )

    def paste_struct(self) -> None:
        self.editor.flush()
        dialog = self.paste_dialog()
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.apply_paste(dialog)

    def apply_paste(self, dialog: PasteStructDialog) -> None:
        stream, summary = dialog.result_stream()
        key = dialog.result_key()
        if key in self.drafts:  # replacing the fields of this stream
            self.drafts[key] = StreamDraft(stream)
            self._rebuild_tabs(key)
            self._on_changed()
            self.status_lbl.setText(f"Fields replaced: {summary}")
        else:
            self._add_stream(key, stream)

    def copy_struct(self) -> None:
        key = self.current_key()
        if key is None:
            return
        self.editor.flush()
        clipboard = QtWidgets.QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(to_c_struct(key, self.drafts[key].to_stream()))
        self.status_lbl.setText(f"Copied {key} as a C struct")

    # --- saving ---

    def save_current(self) -> None:
        self.editor.flush()

    def save_to_file(self) -> None:
        self.save_current()
        try:
            problems = save_document(self.filepath, self.document())
        except InvalidConfigError as e:
            errors = [str(p) for p in e.problems]
            shown = "\n".join(errors[:15]) + ("\n..." if len(errors) > 15 else "")
            QtWidgets.QMessageBox.critical(
                self, "Not saved", f"Fix these problems before saving:\n\n{shown}"
            )
            return
        except OSError as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Save failed: {e}")
            return
        try:
            self.loader.load()  # what Revert goes back to from now on
        except ValueError as e:
            logger.error("Config reload error: %s", e)
        self._saved = self._fingerprint()
        self._on_changed()
        warnings = [str(p) for p in problems]
        note = f" ({len(warnings)} warning(s))" if warnings else ""
        self.status_lbl.setText(f"Saved {self.loader.path.name}{note}")
        self.status_lbl.setToolTip("\n".join(warnings))
        self.config_saved.emit()
