"""
The configuration editor window's content (R7.1): streams.json, laid out like the scope.

- The main window's 36 px bar (R11.1): the profile's name (edited in place), its format
  (read-only: chosen at New profile) and baud, the streams as tabs (right-click deletes one),
  `+` (an empty stream, or from a C struct / console output), the message, `C struct ▾`
  (binary) or `Console ▾` (text), and Revert and Save only while there are unsaved
  changes (Ctrl+S).
- The message is the shown stream's first problem, as validation sees it now, while there
  is one; confirmations (saved, copied) fade.
- The `StreamEditor` for the shown stream.

Each stream is a `StreamDraft`, kept until saved or reverted, so switching streams never
loses an edit (C4a). Saving writes the whole document (schema version, commands and
panels as loaded) through `save_document`, which refuses a file the app couldn't load.
A file with an older schema is saved migrated (R5.1).
"""

from __future__ import annotations

import copy
import json
import logging
from typing import Any

from PyQt6 import QtCore, QtGui, QtWidgets

from core.config import InvalidConfigError, StreamConfigLoader, save_document, validate_stream
from core.config.cstruct import to_c_struct
from core.config.draft import StreamDraft, unique_name
from core.config.profile import FORMAT_LABELS, profile_of
from core.protocol.constants import LOOP_CNTR_NAME
from core.protocol.text_line import PatternError, parse_pattern, printf_line
from styles import AMBER, TEXT_MUTED
from ui.config.console_dialog import ConsoleOutputDialog
from ui.config.paste_dialog import PasteStructDialog
from ui.config.stream_editor import StreamEditor
from ui.panels.connection import BAUD_RATES
from ui.panels.top_bar import MessageLabel, TopBar

logger = logging.getLogger(__name__)

SAVE_DIRTY = (
    f"QPushButton {{ background: {AMBER}; border: 1px solid {AMBER}; color: #000;"
    " font-weight: bold; }"
    " QPushButton:hover { background: #FFC233; border-color: #FFC233; }"
)


NO_BAUD = "—"  # the profile names no baud rate


class ConfiguratorTab(QtWidgets.QWidget):
    config_saved = QtCore.pyqtSignal()
    listen_requested = QtCore.pyqtSignal(float)  # "Listen on the port" (the engine's)
    stop_listening_requested = QtCore.pyqtSignal()

    def __init__(
        self, stream_loader: StreamConfigLoader, parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.loader = stream_loader
        self.filepath: str = str(stream_loader.path)
        self.drafts: dict[str, StreamDraft] = {}
        self._profile: dict[str, Any] | None = None  # the `profile` block, as edited
        self._last_lines: dict[str, str] = {}  # the newest line per stream (link report)
        self._last_unmatched = ""
        self._pasted: dict[str, str] = {}  # a line each stream made from console output matches
        self._connected = False
        self._console: ConsoleOutputDialog | None = None
        self._saved: str = ""  # the document as loaded or saved, to tell unsaved changes
        self._build()
        self._take_document()

    # --- construction ---

    def _build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.bar = TopBar()
        self.profile_name_edit = QtWidgets.QLineEdit()
        self.profile_name_edit.setFixedWidth(130)
        self.profile_name_edit.setToolTip("The profile's name, as the profile menu shows it")
        self.profile_name_edit.setStyleSheet(
            "QLineEdit { background: transparent; border: none; padding: 0 12px; }"
            f" QLineEdit:focus {{ background: #000; border: 1px solid {TEXT_MUTED}; }}"
        )
        self.bar.add(self.profile_name_edit)
        self.format_lbl = QtWidgets.QLabel("")
        self.format_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        self.format_lbl.setToolTip("What the device sends; chosen when the profile is made")
        self.profile_baud_combo = QtWidgets.QComboBox()
        self.profile_baud_combo.setToolTip("The baud rate this profile connects at")
        self.bar.add(self.format_lbl, self.profile_baud_combo, spacing=4)

        self.stream_tabs = QtWidgets.QTabBar()
        self.stream_tabs.setExpanding(False)
        self.stream_tabs.setDrawBase(False)
        self.stream_tabs.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.stream_tabs.setToolTip("Right-click: delete the stream")
        self.stream_tabs.setStyleSheet("QTabBar::tab { padding: 9px 14px 8px 14px; }")
        self.new_act = QtGui.QAction("Empty stream", self)
        self.paste_act = QtGui.QAction("From C struct…", self)
        self.copy_act = QtGui.QAction("Copy as C struct", self)
        self.console_act = QtGui.QAction("From console output…", self)
        self.printf_act = QtGui.QAction("Copy as printf", self)
        self.printf_act.setToolTip("The C line that prints this stream's pattern")
        self.delete_act = QtGui.QAction("Delete stream", self)
        new_menu = QtWidgets.QMenu(self)
        new_menu.addActions([self.new_act, self.paste_act, self.console_act])
        self.new_btn = self._menu_button("+", new_menu, "New stream")
        self.new_btn.setFixedWidth(34)
        self.bar.add(self.stream_tabs, self.new_btn)

        self.status_lbl = MessageLabel()  # the stream's first problem, or what just happened
        self.bar.add_stretch(self.status_lbl)
        tools_menu = QtWidgets.QMenu(self)
        tools_menu.addActions([self.paste_act, self.copy_act, self.console_act, self.printf_act])
        self.tools_btn = self._menu_button("C struct ▾", tools_menu, "")
        self.bar.add_divider()
        self.bar.add(self.tools_btn)

        self.revert_btn = QtWidgets.QPushButton("Revert")
        self.save_btn = QtWidgets.QPushButton("Save")
        self.save_btn.setToolTip("Save streams.json (Ctrl+S)")
        self.save_btn.setStyleSheet(SAVE_DIRTY)
        self.save_box = QtWidgets.QWidget()
        save_row = QtWidgets.QHBoxLayout(self.save_box)
        save_row.setContentsMargins(8, 0, 8, 0)
        save_row.setSpacing(6)
        save_row.addWidget(self.revert_btn)
        save_row.addWidget(self.save_btn)
        self.bar.add_optional(self.save_box)  # only while there are unsaved changes
        layout.addWidget(self.bar)

        self.editor = StreamEditor()
        layout.addWidget(self.editor, 1)

        self.new_act.triggered.connect(self.create_stream)
        self.paste_act.triggered.connect(self.paste_struct)
        self.copy_act.triggered.connect(self.copy_struct)
        self.console_act.triggered.connect(self.from_console_output)
        self.printf_act.triggered.connect(self.copy_printf)
        self.delete_act.triggered.connect(self.delete_stream)
        self.revert_btn.clicked.connect(self.revert)
        self.save_btn.clicked.connect(self.save_to_file)
        self.profile_name_edit.editingFinished.connect(self._on_profile_name)
        self.profile_baud_combo.activated.connect(self._on_profile_baud)
        self.stream_tabs.currentChanged.connect(self._on_tab_changed)
        self.stream_tabs.customContextMenuRequested.connect(self._on_tab_menu)
        self.editor.changed.connect(self._on_changed)
        self.editor.key_rename_requested.connect(self.rename_stream)
        self.editor.problem.connect(self._show_problem)
        save = QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Save, self)
        save.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        save.activated.connect(self.save_to_file)

    @staticmethod
    def _menu_button(text: str, menu: QtWidgets.QMenu, tip: str) -> QtWidgets.QToolButton:
        btn = QtWidgets.QToolButton()
        btn.setText(text)
        btn.setToolTip(tip)
        btn.setMenu(menu)
        btn.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        btn.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Expanding
        )
        btn.setStyleSheet("QToolButton { padding: 0 12px; }")
        return btn

    def _on_tab_menu(self, pos: QtCore.QPoint) -> None:
        index = self.stream_tabs.tabAt(pos)
        if index < 0:
            return
        self.stream_tabs.setCurrentIndex(index)
        menu = QtWidgets.QMenu(self)
        menu.addAction(self.delete_act)
        menu.exec(self.stream_tabs.mapToGlobal(pos))

    # --- document ---

    def load_from_file(self) -> None:
        """Re-reads the file through the shared loader and shows its streams."""
        try:
            self.loader.load()
        except ValueError as e:
            logger.error("Config load error: %s", e)
        self._take_document()

    def reload_document(self) -> None:
        """Edits the file the loader has now (a profile switch, R8.2), as loaded."""
        self.filepath = str(self.loader.path)
        self._last_lines, self._last_unmatched = {}, ""  # another device's lines
        self._pasted = {}
        self._take_document()

    @property
    def is_text(self) -> bool:
        return self.loader.profile.format == "text"

    def _take_document(self) -> None:
        # Edit a private copy of the raw document, including streams with errors, so they
        # can be fixed here.
        block = self.loader.data.get("profile")
        self._profile = copy.deepcopy(block) if isinstance(block, dict) else None
        self._refresh_profile_row()
        self.editor.set_format(self.loader.profile.format)
        for act in (self.paste_act, self.copy_act):  # C structs are for binary frames
            act.setVisible(not self.is_text)
        for act in (self.console_act, self.printf_act):
            act.setVisible(self.is_text)
        self.tools_btn.setText("Console ▾" if self.is_text else "C struct ▾")
        streams = self.loader.data.get("streams", {})
        self.drafts = {str(k): StreamDraft(v) for k, v in streams.items()}
        panels = self.loader.data.get("panels")
        self.editor.set_panel_choices(list(panels) if isinstance(panels, dict) else [])
        self._saved = self._fingerprint()
        shown = self.current_key()
        self._rebuild_tabs(shown if shown in self.drafts else None)
        self._on_changed()

    def document(self) -> dict[str, Any]:
        """The document to save: as loaded, with the edited profile block and streams."""
        doc = dict(self.loader.data)
        if self._profile is not None:
            doc["profile"] = copy.deepcopy(self._profile)
        doc["streams"] = {k: d.to_stream() for k, d in self.drafts.items()}
        return doc

    # --- the profile row (R8.4) ---

    def _profile_block(self) -> dict[str, Any]:
        if self._profile is None:
            self._profile = {}
        return self._profile

    def _edited_profile(self) -> Any:
        doc = {"profile": self._profile} if self._profile is not None else {}
        return profile_of(doc, self.loader.path)

    def _refresh_profile_row(self) -> None:
        profile = self._edited_profile()
        if not self.profile_name_edit.hasFocus():
            self.profile_name_edit.setText(profile.name)
        self.format_lbl.setText(FORMAT_LABELS.get(profile.format, profile.format))
        combo = self.profile_baud_combo
        combo.clear()
        if profile.baud is None:
            combo.addItem(NO_BAUD)
        combo.addItems(BAUD_RATES)
        if profile.baud is not None and combo.findText(str(profile.baud)) < 0:
            combo.addItem(str(profile.baud))
        combo.setCurrentText(str(profile.baud) if profile.baud is not None else NO_BAUD)

    def _on_profile_name(self) -> None:
        name = self.profile_name_edit.text().strip()
        current = self._edited_profile().name
        if not name:
            self.profile_name_edit.setText(current)
        elif name != current:
            self._profile_block()["name"] = name
            self._on_changed()

    def _on_profile_baud(self, _index: int) -> None:
        text = self.profile_baud_combo.currentText()
        if text.isdigit() and self._profile_block().get("baud") != int(text):
            self._profile_block()["baud"] = int(text)
            self._refresh_profile_row()
            self._on_changed()

    # --- last lines (R8.4) ---

    def set_last_lines(self, lines: dict[str, str], unmatched: str) -> None:
        """The newest line each stream matched and the newest unmatched one (~1 Hz)."""
        self._last_lines = dict(lines)
        self._last_unmatched = unmatched
        self._show_last_line()

    def _show_last_line(self) -> None:
        key = self.current_key() or ""
        line = self._last_lines.get(key) or self._pasted.get(key) or self._last_unmatched
        self.editor.set_last_line(line or None)

    # --- console output and printf (R8.4) ---

    def set_connected(self, connected: bool) -> None:
        """Whether the dashboard is connected: listening needs a live port."""
        self._connected = connected
        if self._console is not None:
            self._console.set_connected(connected)

    def on_lines_heard(self, lines: list[str]) -> None:
        if self._console is not None:
            self._console.on_lines_heard(lines)

    def console_dialog(self) -> ConsoleOutputDialog:
        dialog = ConsoleOutputDialog(self._edited_profile().name, self._connected, self)
        dialog.listen_requested.connect(self.listen_requested)
        dialog.stop_requested.connect(self.stop_listening_requested)
        return dialog

    def from_console_output(self) -> None:
        self.editor.flush()
        dialog = self.console_dialog()
        self._console = dialog
        try:
            if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
                self.apply_console(dialog)
        finally:
            self._console = None

    def apply_console(self, dialog: ConsoleOutputDialog) -> None:
        """Adds the ticked patterns as new streams (keys made from their names)."""
        created: list[str] = []
        for name, stream, line in dialog.results():
            key = unique_name(name.lower(), self.drafts, fallback="stream")
            self.drafts[key] = StreamDraft(stream)
            self._pasted[key] = line
            created.append(key)
        if not created:
            return
        self._rebuild_tabs(created[0])
        self._on_changed()
        self.status_lbl.say(f"Created {', '.join(created)} from console output")

    def copy_printf(self) -> None:
        key = self.current_key()
        if key is None:
            return
        self.editor.flush()
        draft = self.drafts[key]
        try:
            pattern = parse_pattern(draft.pattern or "")
        except PatternError as e:
            self._show_problem(f"Pattern: {e}")
            return
        types = {f.name: f.type for f in draft.layout()}
        clipboard = QtWidgets.QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(printf_line(pattern, types))
        self.status_lbl.say(f"Copied {key} as printf", "ok")

    def _fingerprint(self) -> str:
        return json.dumps(self.document())

    def is_dirty(self) -> bool:
        return self._fingerprint() != self._saved

    # --- tabs ---

    def _tab_text(self, draft: StreamDraft) -> str:
        name = draft.name or "(unnamed)"
        return name if self.is_text else f"{name} · 0x{draft.stream_id:02X}"

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
        self._show_last_line()
        self._update_status()

    # --- edits ---

    def _on_changed(self) -> None:
        key = self.current_key()
        for i in range(self.stream_tabs.count()):  # a flush on a switch edits the one left
            draft = self.drafts.get(self.stream_tabs.tabData(i))
            if draft is not None:
                self.stream_tabs.setTabText(i, self._tab_text(draft))
        self.bar.set_shown(self.save_box, self.is_dirty())
        has_stream = key is not None
        for act in (self.copy_act, self.printf_act, self.delete_act):
            act.setEnabled(has_stream)
        self._update_status()

    def _update_status(self) -> None:
        """The bar's message: the shown stream's first problem while there is one."""
        key = self.current_key()
        if key is None or key not in self.drafts:
            self.status_lbl.say("No streams")
            return
        if self.editor.pattern_error is not None:
            self.status_lbl.say(
                self.editor.pattern_error,
                "error",
                "Not applied: fix the pattern, or Esc to drop the edit",
            )
            return
        draft = self.drafts[key]
        problems = validate_stream(key, draft.to_stream(), self.loader.profile.format)
        errors = [p for p in problems if p.severity == "error"]
        shown = errors or problems
        if shown:
            more = f" (+{len(shown) - 1} more)" if len(shown) > 1 else ""
            self.status_lbl.say(
                f"{shown[0].message}{more}",
                "error" if errors else "warn",
                "\n".join(str(p) for p in problems),
            )
        elif self.status_lbl.level in ("warn", "error"):
            self.status_lbl.say("")  # fixed: a confirmation fades by itself

    def _show_problem(self, message: str) -> None:
        if self.editor.pattern_error is not None:
            self._update_status()  # the pattern's error
            return
        self.status_lbl.say(message, "error")

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
        if self.is_text:
            fields = [{"name": "v1", "type": "f32"}]
            stream = {"name": "New stream", "frame": {"pattern": "new,{v1}", "fields": fields}}
            self._add_stream(key, {**stream, "signals": {}})
            return
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
            self.status_lbl.say(f"Fields replaced: {summary}", "ok")
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
        self.status_lbl.say(f"Copied {key} as a C struct", "ok")

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
        self.status_lbl.say(f"Saved {self.loader.path.name}{note}", "ok", "\n".join(warnings))
        self.config_saved.emit()
