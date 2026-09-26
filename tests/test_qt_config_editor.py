"""
The configuration editor in the scope look (R7.1) and C structs in and out (R7.2, R7.3).

Marked `qt`: skipped when pytest-qt is disabled or Qt can't load (see tests/conftest.py).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6 import QtCore, QtWidgets  # noqa: E402

pytestmark = pytest.mark.qt

REPO_ROOT = Path(__file__).resolve().parent.parent

ODOM = """
#define TELEM_ID_ODOM 0x04

typedef struct __attribute__((packed)) {
    uint32_t loop_cntr;
    float    x_m, y_m;
    float    heading_rad;
    int16_t  wheel_ticks[2];
    uint16_t batt_mv;
    uint8_t  flags;
} odom_frame_t;
"""


@pytest.fixture
def boxes(monkeypatch: Any) -> list[tuple[str, str]]:
    """Records QMessageBox calls as (kind, text) instead of opening modal dialogs."""
    calls: list[tuple[str, str]] = []

    def recorder(kind: str) -> Any:
        def record(*args: Any, **_kw: Any) -> int:
            calls.append((kind, str(args[2])))
            return 0

        return staticmethod(record)

    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(QtWidgets.QMessageBox, kind, recorder(kind))
    return calls


@pytest.fixture
def config(tmp_path: Path, boxes: Any) -> Path:
    path = tmp_path / "streams.json"
    path.write_bytes((REPO_ROOT / "streams.json").read_bytes())
    return path


def _tab(qtbot: Any, path: Path, key: str = "imu_6axis") -> Any:
    from core.config import StreamConfigLoader
    from ui.config.tab import ConfiguratorTab

    tab = ConfiguratorTab(StreamConfigLoader(path))
    qtbot.addWidget(tab)
    tab.select_stream(key)
    return tab


def _saved(path: Path) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return doc


def test_clicking_a_field_in_the_frame_selects_it_everywhere(qtbot: Any, config: Path) -> None:
    from ui.config.stream_editor import ROLE_FIELD

    tab = _tab(qtbot, config)
    tab.resize(1300, 800)
    tab.show()
    qtbot.waitExposed(tab)
    view = tab.editor.frame_strip
    piece = next(p for p in view.pieces() if p.slot.name == "acc_z")

    qtbot.mouseClick(view, QtCore.Qt.MouseButton.LeftButton, pos=piece.rect.center().toPoint())

    editor = tab.editor
    assert editor.selected_field == "acc_z" and editor.selected_signal == "acc_z"
    assert view.selected == editor.draft.field_names().index("acc_z")
    current = editor.tree.currentItem()
    assert current is not None and current.data(0, ROLE_FIELD) == "acc_z"
    assert editor.field_name_edit.text() == "acc_z"
    assert editor.field_byte_lbl.text().startswith("13 ")
    assert editor.label_edit.text() == "Acc Z"


def test_the_tree_groups_signals_by_lane_and_lists_unplotted_fields(
    qtbot: Any, config: Path
) -> None:
    tab = _tab(qtbot, config)
    tree = tab.editor.tree
    lanes = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    assert [i.text(0) for i in lanes if i is not None] == [
        "ACCELEROMETER g",
        "GYROSCOPE dps",
        "NOT PLOTTED",
    ]
    assert tree.isColumnHidden(2)  # no pairs: one Field column
    unplotted = lanes[2]
    assert unplotted is not None
    rows = [unplotted.child(i) for i in range(unplotted.childCount())]
    assert [(r.text(0), r.text(1)) for r in rows if r is not None] == [
        ("X axis", "loop_cntr"),
        ("", "motor"),
        ("", "gyro_y"),
    ]


def test_dragging_plots_moves_and_unplots(qtbot: Any, config: Path) -> None:
    from ui.config.stream_editor import FIELD_MARK, UNPLOTTED_LANE
    from ui.panels.signals import NEW_LANE

    tab = _tab(qtbot, config)
    editor = tab.editor

    editor.tree.dropped.emit(FIELD_MARK + "gyro_y", "gyro")  # a field onto a lane: plotted
    assert editor.draft.signal("gyro_y")["group"] == "gyro"
    editor.tree.dropped.emit("acc_x", NEW_LANE)  # a signal onto empty space: a new lane
    lane = editor.draft.signal("acc_x")["group"]
    assert editor.draft.lane_label(lane) == "Lane 4"
    editor.tree.dropped.emit("acc_y", UNPLOTTED_LANE)  # onto "Not plotted": removed
    assert "acc_y" not in editor.draft.signals
    assert tab.is_dirty()


def test_pressing_a_swatch_hides_the_signal_when_the_stream_opens(qtbot: Any, config: Path) -> None:
    from ui.config.signal_list import ROLE_CELL

    tab = _tab(qtbot, config)
    tab.resize(1300, 800)
    tab.show()
    qtbot.waitExposed(tab)
    tree = tab.editor.tree
    accel = tree.topLevelItem(0)
    assert accel is not None
    item = accel.child(0)
    assert item is not None and item.data(1, ROLE_CELL).key == "acc_x"
    rect = tree.visualItemRect(item)
    swatch = QtCore.QPoint(tree.columnViewportPosition(1) + 12, rect.center().y())
    qtbot.mouseClick(tree.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=swatch)
    assert tab.editor.draft.signal("acc_x")["visible"] is False

    def redrawn() -> bool:  # the redraw runs on the next turn, on fresh items
        lane = tree.topLevelItem(0)
        row = lane.child(0) if lane is not None else None
        return row is not None and row.data(1, ROLE_CELL).shown is False

    qtbot.waitUntil(redrawn)
    assert tab.editor.selected_field == "motor"  # a swatch doesn't move the selection


def test_the_pid_stream_lists_one_row_per_pair(qtbot: Any, config: Path) -> None:
    tab = _tab(qtbot, config, "pid")
    tree = tab.editor.tree
    rows = [
        lane.child(j)
        for lane in (tree.topLevelItem(i) for i in range(tree.topLevelItemCount()))
        if lane is not None
        for j in range(lane.childCount())
    ]
    assert len(rows) == 17 + 1  # 34 signals in pairs, and loop_cntr not plotted
    assert [tree.headerItem().text(c) for c in range(3)] == ["Signal", "L", "R"]


def test_l_equals_r_gives_the_other_side_colour_lane_and_width(qtbot: Any, config: Path) -> None:
    tab = _tab(qtbot, config, "pid")
    editor = tab.editor
    editor.select("left_setpoint", "left_setpoint")
    assert editor.link_btn.isVisibleTo(editor) and editor.linked
    editor._set_signal("color", "#123456")
    editor.width_group.button(1).click()  # 2 px
    right = editor.draft.signal("right_setpoint")
    assert right["color"] == "#123456" and right["line"]["width"] == 2
    assert right["line"]["style"] == "dashed"  # R stays dashed
    editor.label_edit.setText("L: Speed setpoint")
    editor.label_edit.editingFinished.emit()
    assert editor.draft.signal("right_setpoint")["label"] == "R: Setpoint"  # per side

    editor.link_btn.setChecked(False)
    editor._set_signal("color", "#654321")
    assert editor.draft.signal("right_setpoint")["color"] == "#123456"


def test_edits_keep_what_the_editor_doesnt_show(qtbot: Any, config: Path) -> None:
    tab = _tab(qtbot, config)
    editor = tab.editor
    editor.select("acc_x")
    editor.label_edit.setText("Accel X")
    editor.label_edit.editingFinished.emit()
    editor.width_group.button(1).click()  # 2 px

    tab.save_to_file()

    saved = _saved(config)["streams"]["imu_6axis"]["signals"]["acc_x"]
    assert saved["label"] == "Accel X" and saved["line"]["width"] == 2
    assert saved["y_range"] == {"min": -2.0, "max": 2.0}  # not shown, still there
    assert not tab.is_dirty()


def test_revert_goes_back_to_the_saved_document(qtbot: Any, config: Path) -> None:
    tab = _tab(qtbot, config)
    tab.editor.select("acc_x")
    assert not tab.save_btn.isVisibleTo(tab)  # nothing to save: no Revert or Save
    tab.editor.remove_field_btn.click()
    assert tab.is_dirty() and tab.save_btn.isVisibleTo(tab)
    assert tab.revert_btn.isVisibleTo(tab)

    tab.revert()

    assert not tab.is_dirty() and not tab.save_btn.isVisibleTo(tab)
    assert tab.current_key() == "imu_6axis"
    assert "acc_x" in tab.editor.draft.field_names()


def test_renaming_new_and_deleting_streams(
    qtbot: Any, config: Path, boxes: list[tuple[str, str]]
) -> None:
    tab = _tab(qtbot, config)
    tab.editor.key_edit.setText("imu")
    tab.editor.key_edit.editingFinished.emit()
    assert list(tab.drafts) == ["pid", "pid_ff", "imu"]
    assert tab.current_key() == "imu"

    tab.editor.key_edit.setText("pid")
    tab.editor.key_edit.editingFinished.emit()
    assert boxes[-1][0] == "warning" and list(tab.drafts)[2] == "imu"

    tab.create_stream()
    assert tab.current_key() == "new_stream"
    assert tab.editor.draft.stream_id not in (1, 3)
    tab.delete_stream()
    assert list(tab.drafts) == ["pid", "pid_ff", "imu"]


def test_a_pasted_struct_becomes_a_new_stream(
    qtbot: Any, config: Path, boxes: list[tuple[str, str]]
) -> None:
    tab = _tab(qtbot, config)
    dialog = tab.paste_dialog()
    qtbot.addWidget(dialog)
    assert not dialog.ok_btn.isEnabled()

    dialog.set_source(ODOM)

    assert dialog.ok_btn.isEnabled() and dialog.ok_btn.text() == "Create stream"
    assert dialog.key_edit.text() == "odom_frame_t" and dialog.id_spin.value() == 4
    assert dialog.size_lbl.text() == "23 / 255 B · packed"
    assert len(dialog.frame_view.pieces()) == 8
    assert dialog.fields_tree.topLevelItemCount() == 8
    assert not dialog.problems_lbl.isVisible()

    tab.apply_paste(dialog)

    assert tab.current_key() == "odom_frame_t"
    assert tab.editor.draft.time_value("scale_s") == 0.005  # like the stream shown before
    tab.save_to_file()
    assert boxes == []
    odom = _saved(config)["streams"]["odom_frame_t"]
    assert odom["frame"]["stream_id"] == 4
    assert list(odom["signals"]) == [
        "x_m", "y_m", "heading_rad", "wheel_ticks_0", "wheel_ticks_1", "batt_mv", "flags",
    ]  # fmt: skip


def test_pasting_can_replace_the_fields_of_the_shown_stream(qtbot: Any, config: Path) -> None:
    tab = _tab(qtbot, config)
    before = tab.editor.draft.signal("acc_x")
    dialog = tab.paste_dialog()
    qtbot.addWidget(dialog)
    dialog.into_combo.setCurrentIndex(dialog.into_combo.findData("replace"))
    dialog.set_source(
        "uint32_t loop_cntr; uint8_t motor; float acc_x, acc_y, acc_z, gyro_x, gyro_y, temp;"
    )
    assert dialog.ok_btn.text() == "Replace fields"
    assert "7 field(s) kept, 1 added, 1 removed" in dialog.summary_lbl.text()
    assert not dialog.key_edit.isEnabled()

    tab.apply_paste(dialog)

    draft = tab.editor.draft
    assert list(tab.drafts) == ["pid", "pid_ff", "imu_6axis"]
    assert draft.signal("acc_x") == before
    assert "gyro_z" not in draft.signals and draft.signal("temp")["field"] == "temp"
    assert "kept" in tab.status_lbl.text()


def test_problems_in_the_paste_are_listed(qtbot: Any, config: Path) -> None:
    tab = _tab(qtbot, config)
    dialog = tab.paste_dialog()
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.set_source("struct s { uint32_t loop_cntr; long x; uint8_t a; float b; };")
    assert dialog.problems_lbl.isVisible()
    text = dialog.problems_lbl.text()
    assert "long x" in text and "padding" in text


def test_copy_as_c_struct(qtbot: Any, config: Path) -> None:
    tab = _tab(qtbot, config)
    tab.copy_act.trigger()
    clipboard = QtWidgets.QApplication.clipboard()
    assert clipboard is not None
    text = clipboard.text()
    assert "} imu_6axis_t;" in text and "sizeof(imu_6axis_t) == 29" in text
    assert "Copied" in tab.status_lbl.text()
