"""
The Signals pane (R9.4): one row per pair, swatches as toggles, the filter on demand, and
the readout at A in the trace's colour.

Marked `qt`: skipped when pytest-qt is disabled or Qt can't load (see tests/conftest.py).
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6 import QtCore  # noqa: E402

from core.config import DEFAULT_CONFIG_PATH, StreamConfigLoader  # noqa: E402

pytestmark = pytest.mark.qt


def _pane(qtbot: Any) -> Any:
    from ui.panels.signals import SignalListPanel

    panel = SignalListPanel()
    qtbot.addWidget(panel)
    panel.resize(300, 800)
    panel.rebuild_list(StreamConfigLoader(DEFAULT_CONFIG_PATH).get_stream("pid"))
    return panel


def test_the_bundled_pid_stream_shows_17_paired_rows(qtbot: Any) -> None:
    from ui.panels.signals import LEFT_COLUMN, RIGHT_COLUMN, ROLE_SWATCH

    panel = _pane(qtbot)
    rows = panel.rows()
    assert len(rows) == 17 and all(r.left and r.right for r in rows)
    assert rows[0].name == "Target Setpoint"
    header = panel.tree.headerItem()
    assert [header.text(i) for i in range(3)] == ["Signals", "L", "R"]
    lane = panel.tree.topLevelItem(0)
    assert lane is not None and lane.text(0) == "SPEED rps"
    item = panel._items["left_measurement"]
    assert item is panel._items["right_measurement"]  # one row for the pair
    color, shown, dashed = item.data(LEFT_COLUMN, ROLE_SWATCH)
    assert (color, shown, dashed) == ("#FFE81A", True, False)
    assert item.data(RIGHT_COLUMN, ROLE_SWATCH) == ("#FFE81A", True, True)  # R: dashed
    assert not hasattr(panel, "count_lbl")  # no "26 of 34 shown"


def test_a_swatch_click_toggles_its_signal(qtbot: Any) -> None:
    from ui.panels.signals import RIGHT_COLUMN, ROLE_SWATCH

    panel = _pane(qtbot)
    changes: list[tuple[str, bool]] = []
    panel.signal_visibility_changed.connect(lambda *a: changes.append(a))
    item = panel._items["right_error"]
    panel.tree.itemClicked.emit(item, RIGHT_COLUMN)
    assert changes == [("right_error", False)] and not panel.is_visible("right_error")
    assert item.data(RIGHT_COLUMN, ROLE_SWATCH)[1] is False  # hollow now
    assert panel.is_visible("left_error")  # its pair stays
    panel.tree.itemClicked.emit(item, 0)  # the name: no toggle
    assert len(changes) == 1
    panel.set_lane_visible("speed", False)
    assert not any(panel.is_visible(s) for s in ("left_setpoint", "right_measurement"))


def test_the_filter_appears_on_typing_and_hides_on_escape(qtbot: Any) -> None:
    panel = _pane(qtbot)
    with qtbot.waitActive(panel):  # shortcuts only reach the active window
        panel.show()
        panel.activateWindow()
    assert panel.filter_edit.isHidden()
    panel.tree.setFocus()
    qtbot.keyClicks(panel.tree, "pwm")
    assert not panel.filter_edit.isHidden() and panel.filter_edit.text() == "pwm"
    shown = [r.name for r in panel.rows() if not panel._items[r.members[0]].isHidden()]
    assert shown == ["PWM cmd"]
    qtbot.keyClick(panel.filter_edit, QtCore.Qt.Key.Key_Escape)
    assert panel.filter_edit.isHidden() and panel.filter_edit.text() == ""
    assert not panel._items["left_error"].isHidden()
    panel.tree.setFocus()
    qtbot.keyClick(panel.tree, QtCore.Qt.Key.Key_F, QtCore.Qt.KeyboardModifier.ControlModifier)
    assert not panel.filter_edit.isHidden()


def test_readout_shows_values_at_a_in_colour_and_the_a_b_header(qtbot: Any) -> None:
    from ui.charts.series import Readout

    panel = _pane(qtbot)
    assert panel.cursor_lbl.isHidden()
    panel.set_visible("right_error", False)
    values = {"left_measurement": 0.26723, "right_measurement": 159.5349, "right_error": 1.0}
    panel.show_readout(Readout(0.42, -0.34, values, {"left_measurement": 0.01}))
    assert panel.cursor_lbl.text() == "A 0.42s  B 0.76s  ΔT +0.34s"
    assert not panel.cursor_lbl.isHidden()
    assert panel.value_text("left_measurement") == "0.267"
    assert panel.value_text("right_measurement") == "159.5"
    assert panel.value_text("right_error") == ""  # hidden: no value
    item = panel._items["left_measurement"]
    assert item.text(1) == "0.267" and "Δ (A − B) +0.01" in item.toolTip(1)
    panel.show_readout(None)
    assert panel.cursor_lbl.isHidden() and panel.value_text("left_measurement") == ""


def test_moving_a_row_moves_the_pair(qtbot: Any) -> None:
    from ui.panels.signals import NEW_LANE

    panel = _pane(qtbot)
    moves: list[tuple[str, str, str]] = []
    panel.signal_lane_changed.connect(lambda *a: moves.append(a))
    panel._move_row_of("left_u_ff", NEW_LANE)
    assert [m[0] for m in moves] == ["left_u_ff", "right_u_ff"]
    assert panel.lane_of("left_u_ff") == panel.lane_of("right_u_ff") == moves[0][1]
    panel.move_to_lane("left_u_ff", "control")  # one signal alone still works (API)
    assert panel.row_of("left_u_ff").right is None and panel.row_of("right_u_ff").left is None
