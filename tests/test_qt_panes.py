"""
Panes and edge tabs (R9.3, ADR-0012 decision 2): the Signals pane, the plot and the right
pane (Tune / Step, or the terminal) in a splitter, opened and closed from always-visible
edge tabs and `[`, `]`, `\\`, remembered per profile.

Marked `qt`: skipped when pytest-qt is disabled or Qt can't load (see tests/conftest.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6 import QtCore  # noqa: E402

from core.config import DEFAULT_CONFIG_PATH  # noqa: E402

pytestmark = pytest.mark.qt

TEXT_PROFILE = Path(__file__).parent / "fixtures" / "text_profile.json"


def _window(qtbot: Any, settings_file: Path, config: Path = DEFAULT_CONFIG_PATH) -> Any:
    from ui.main_window import MainWindow

    settings = QtCore.QSettings(str(settings_file), QtCore.QSettings.Format.IniFormat)
    win = MainWindow(config, settings=settings)
    qtbot.addWidget(win)
    return win


def _open(win: Any) -> tuple[bool, bool, str]:
    return (not win.panel.sig_panel.isHidden(), not win.right_stack.isHidden(), win.panes.view)


def test_pane_state_toggles() -> None:
    from ui.panes import PaneState

    s = PaneState()
    assert (s.left_open, s.right_open, s.view) == (True, True, "tune")
    assert not s.toggle_left().left_open and not s.toggle_right().right_open
    both = s.toggle_both()
    assert not both.left_open and not both.right_open
    assert both.toggle_both().left_open and both.toggle_both().right_open
    only_left = s.toggle_right().toggle_both()  # anything open: focus closes both
    assert not only_left.left_open and not only_left.right_open
    step = s.click_right("step")  # another view: opens on it
    assert step.right_open and step.view == "step"
    assert not step.click_right("step").right_open  # its lit tab: closes
    assert step.click_right("step").click_right("tune").view == "tune"


def test_pane_state_reads_what_it_can() -> None:
    from ui.panes import MIN_PANE_WIDTH, PaneState

    assert PaneState.from_json(None) == PaneState()
    state = PaneState(left_open=False, view="step", left_width=300, right_width=420)
    assert PaneState.from_json(state.to_json()) == state
    odd = PaneState.from_json(
        {"view": "scope", "left_open": 1, "left_width": 5, "right_width": "x"}
    )
    assert odd.view == "tune" and odd.left_open and odd.left_width == MIN_PANE_WIDTH
    assert odd.right_width == PaneState().right_width


def test_edge_tabs_open_and_close_their_panes(qtbot: Any, tmp_path: Path) -> None:
    win = _window(qtbot, tmp_path / "s.ini")
    assert _open(win) == (True, True, "tune")
    assert win.signals_tab.lit and win.tune_tab.lit and not win.step_tab.lit
    win.signals_tab.click()
    assert _open(win)[0] is False and not win.signals_tab.lit
    win.step_tab.click()  # an unlit right tab: the pane shows that view
    assert _open(win)[1:] == (True, "step")
    assert win.right_stack.currentWidget() is win.panel.trigger_panel.results
    assert win.step_tab.lit and not win.tune_tab.lit
    win.step_tab.click()
    assert _open(win)[1] is False and not win.step_tab.lit
    win.tune_tab.click()
    assert _open(win)[1:] == (True, "tune") and win.right_stack.currentWidget() is win.controls_view
    assert win.act_right_pane.isChecked() and not win.act_signals_pane.isChecked()


def test_keys_toggle_panes_but_not_while_typing(qtbot: Any, tmp_path: Path) -> None:
    win = _window(qtbot, tmp_path / "s.ini")
    with qtbot.waitActive(win):  # shortcuts only reach the active window
        win.show()
        win.activateWindow()
    win.plot.setFocus()
    qtbot.keyClick(win.plot, "[")
    assert _open(win)[:2] == (False, True)
    qtbot.keyClick(win.plot, "]")
    assert _open(win)[:2] == (False, False)
    qtbot.keyClick(win.plot, "\\")  # both closed: plot-only ends, both open
    assert _open(win)[:2] == (True, True)
    qtbot.keyClick(win.plot, "\\")
    assert _open(win)[:2] == (False, False)
    qtbot.keyClick(win.plot, "\\")

    win.panel.sig_panel.show_filter()  # the filter (shown on Ctrl+F) keeps its keys
    edit = win.panel.sig_panel.filter_edit
    qtbot.keyClicks(edit, "[x]\\")  # typed text, not pane toggles
    assert edit.text() == "[x]\\" and _open(win)[:2] == (True, True)


def test_panes_are_remembered_per_profile_and_across_runs(qtbot: Any, tmp_path: Path) -> None:
    settings_file = tmp_path / "s.ini"
    win = _window(qtbot, settings_file)
    win.toggle_signals_pane()
    win.show_right_view("step")
    assert win.switch_profile(TEXT_PROFILE)
    assert _open(win) == (True, True, "tune")  # its own (default) panes
    assert win.step_tab.isHidden() and win.tune_tab.text() == "TERMINAL"
    win.step_tab.click()  # hidden for a text profile; even a click can't show Step
    win.toggle_right_pane()
    assert win.switch_profile(DEFAULT_CONFIG_PATH)
    assert _open(win) == (False, True, "step") and not win.step_tab.isHidden()
    win.close()
    win.settings.sync()

    again = _window(qtbot, settings_file)
    assert _open(again) == (False, True, "step") and again.step_tab.lit
    assert again.switch_profile(TEXT_PROFILE)
    assert _open(again)[:2] == (True, False)


def test_pane_widths_come_back(qtbot: Any, tmp_path: Path) -> None:
    from ui.panes import PaneState

    settings_file = tmp_path / "s.ini"
    win = _window(qtbot, settings_file)
    win.ui_state.set_panes(PaneState(left_width=310, right_width=400))
    win.settings.sync()
    win.close()

    again = _window(qtbot, settings_file)
    again.resize(1400, 800)
    again.show()
    qtbot.waitExposed(again)
    left, plot, right = again.splitter.sizes()
    assert (left, right) == (310, 400) and plot > 0
    again.splitter.moveSplitter(260, 1)  # the user drags the Signals pane narrower
    assert again.ui_state.panes().left_width == again.splitter.sizes()[0] < 310
