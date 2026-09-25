"""
The configuration editor on a text profile (R8.4): the profile row, the Pattern field,
the X axis and the Line view.

Marked `qt`: skipped when pytest-qt is disabled or Qt can't load (see tests/conftest.py).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6 import QtCore, QtGui  # noqa: E402

pytestmark = pytest.mark.qt

FIXTURE = Path(__file__).parent / "fixtures" / "text_profile.json"


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    """The text fixture, written the way the editor saves (so an untouched save is equal)."""
    path = tmp_path / "arduino-imu.json"
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    path.write_text(json.dumps(doc, indent=4), encoding="utf-8")
    return path


def _tab(qtbot: Any, path: Path, key: str = "imu") -> Any:
    from core.config import StreamConfigLoader
    from ui.config.tab import ConfiguratorTab

    tab = ConfiguratorTab(StreamConfigLoader(path))
    qtbot.addWidget(tab)
    tab.resize(1300, 800)
    tab.show()
    tab.select_stream(key)
    return tab


def _saved(path: Path) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return doc


def _type_pattern(editor: Any, text: str) -> None:
    editor.pattern_edit.setText(text)
    editor.pattern_edit.textEdited.emit(text)
    editor.pattern_edit.editingFinished.emit()


def test_a_text_profile_lays_the_editor_out_for_lines(qtbot: Any, profile: Path) -> None:
    tab = _tab(qtbot, profile)
    editor = tab.editor
    assert tab.format_lbl.text() == "Text lines"
    assert tab.profile_name_edit.text() == "arduino-imu"
    assert tab.profile_baud_combo.currentText() == "115200"
    assert editor.pattern_edit.isVisible() and not editor.id_spin.isVisible()
    assert editor.line_view.isVisible() and not editor.frame_view.isVisible()
    assert not tab.paste_btn.isVisible() and not tab.copy_btn.isVisible()
    assert editor.pattern_edit.text() == "IMU,{ms},{ax},{ay},{az}"
    assert [tab.stream_tabs.tabText(i) for i in range(2)] == ["IMU", "Environment"]
    assert editor.size_lbl.text() == "4 values"
    assert editor.form_title.text() == "Value"
    # The X axis offers the integer values and the line number.
    combo = editor.time_field_combo
    assert [combo.itemText(i) for i in range(combo.count())] == ["ms", "(line number)"]
    assert combo.currentText() == "ms" and editor.time_step_edit.isVisible()


def test_an_untouched_text_profile_saves_byte_identically(qtbot: Any, profile: Path) -> None:
    original = profile.read_bytes()
    tab = _tab(qtbot, profile)
    tab.select_stream("env")
    tab.select_stream("imu")
    assert not tab.is_dirty()
    tab.save_to_file()
    assert profile.read_bytes() == original


def test_the_profile_row_edits_the_profile_block(qtbot: Any, profile: Path) -> None:
    tab = _tab(qtbot, profile)
    tab.profile_name_edit.setText("imu-bench")
    tab.profile_name_edit.editingFinished.emit()
    tab.profile_baud_combo.setCurrentText("230400")
    tab.profile_baud_combo.activated.emit(tab.profile_baud_combo.currentIndex())
    assert tab.is_dirty()

    tab.save_to_file()

    saved = _saved(profile)
    assert saved["profile"] == {"name": "imu-bench", "format": "text", "baud": 230400}
    assert list(saved) == ["schema_version", "profile", "streams"]  # the block kept its place


def test_a_pattern_edit_updates_the_line_view_and_the_values(qtbot: Any, profile: Path) -> None:
    tab = _tab(qtbot, profile)
    editor = tab.editor
    _type_pattern(editor, "IMU,{ms},{ax},{az},{temp}")

    assert editor.draft.field_names() == ["ms", "ax", "az", "temp"]
    assert set(editor.draft.signals) == {"ax", "az"}  # ay's signal went with its slot
    blocks = [p.text for p in editor.line_view.pieces() if p.index is not None]
    assert blocks == ["ms", "ax", "az", "temp"]
    assert tab.is_dirty()

    editor.select("temp")
    assert editor.field_byte_lbl.text() == "4 of 4"
    assert editor.field_type_combo.currentText() == "number"


def test_an_invalid_pattern_is_not_applied_and_esc_drops_it(qtbot: Any, profile: Path) -> None:
    tab = _tab(qtbot, profile)
    editor = tab.editor
    _type_pattern(editor, "IMU,{ms}{ax},{ay},{az}")

    assert editor.pattern_error == "'{ms}' and '{ax}' need fixed text between them"
    assert editor.draft.pattern == "IMU,{ms},{ax},{ay},{az}"  # not applied
    assert editor.pattern_edit.text() == "IMU,{ms}{ax},{ay},{az}"  # still there to fix
    assert "#ff6b6b" in editor.pattern_edit.styleSheet()
    assert "need fixed text between them" in tab.status_lbl.text()
    assert editor.line_view._dimmed
    assert not tab.is_dirty()

    qtbot.keyClick(editor.pattern_edit, QtCore.Qt.Key.Key_Escape)

    assert editor.pattern_error is None
    assert editor.pattern_edit.text() == "IMU,{ms},{ax},{ay},{az}"
    assert "#ff6b6b" not in editor.pattern_edit.styleSheet()
    assert "need fixed text" not in tab.status_lbl.text()


def test_the_line_number_as_x_axis_hides_the_step(qtbot: Any, profile: Path) -> None:
    tab = _tab(qtbot, profile)
    editor = tab.editor
    combo = editor.time_field_combo
    combo.setCurrentIndex(combo.findText("(line number)"))
    combo.activated.emit(combo.currentIndex())

    assert editor.draft.data["time"]["field"] == "_line"
    assert not editor.time_step_edit.isVisible() and editor.time_unit_lbl.isVisible()

    tab.select_stream("env")  # no counter: the line number by default
    assert editor.time_field_combo.currentText() == "(line number)"
    assert "field" not in editor.draft.data.get("time", {})


def test_add_and_remove_value_edit_the_pattern(qtbot: Any, profile: Path) -> None:
    tab = _tab(qtbot, profile, "env")
    editor = tab.editor
    editor.select("t")
    editor.add_field_btn.click()
    assert editor.pattern_edit.text() == "ENV t={t},{v3}C h={h}%"
    assert editor.selected_field == "v3"
    editor.remove_field_btn.click()
    assert editor.pattern_edit.text() == "ENV t={t}C h={h}%"


def test_clicking_a_value_block_selects_it(qtbot: Any, profile: Path) -> None:
    tab = _tab(qtbot, profile)
    editor = tab.editor
    piece = next(p for p in editor.line_view.pieces() if p.text == "az")
    qtbot.mouseClick(
        editor.line_view, QtCore.Qt.MouseButton.LeftButton, pos=piece.rect.center().toPoint()
    )
    assert editor.selected_field == "az"
    assert editor.field_name_edit.text() == "az"


def test_the_line_view_shows_the_newest_line_and_whether_it_matches(
    qtbot: Any, profile: Path
) -> None:
    tab = _tab(qtbot, profile)
    view = tab.editor.line_view
    assert view.line is None and view.matches() is None  # "none yet"
    tab.set_last_lines({"imu": "IMU,10,0.5,-1,0.98"}, "IMU,10,0.5")
    assert view.line == "IMU,10,0.5,-1,0.98" and view.matches()
    tab.select_stream("env")  # nothing matched it: the newest unmatched line
    assert view.line == "IMU,10,0.5" and view.matches() is False
    view.grab()  # paints without errors
    assert isinstance(view.grab(), QtGui.QPixmap)


def test_a_new_stream_in_a_text_profile_is_a_pattern(qtbot: Any, profile: Path) -> None:
    tab = _tab(qtbot, profile)
    tab.new_btn.click()
    assert tab.editor.draft.pattern == "new,{v1}"
    assert "new_stream" in tab.drafts
    tab.save_to_file()
    assert _saved(profile)["streams"]["new_stream"]["frame"] == {
        "pattern": "new,{v1}",
        "fields": [{"name": "v1", "type": "f32"}],
    }
