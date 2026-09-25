"""
The Tune and Step panes (R9.5): Manual | Live, Presets ▾, one L=R box, square inputs
without arrows, Send per column, Revert N only while edited, the send log as plain lines,
and the Step metrics as Now / Prev / Δ.

Marked `qt`: skipped when pytest-qt is disabled or Qt can't load (see tests/conftest.py).
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6 import QtCore, QtGui, QtWidgets  # noqa: E402

from core.config import DEFAULT_CONFIG_PATH, StreamConfigLoader  # noqa: E402

pytestmark = pytest.mark.qt


def _panel(qtbot: Any) -> Any:
    from ui.panels.command_panel import CommandPanel

    panel = CommandPanel(StreamConfigLoader(DEFAULT_CONFIG_PATH).panels["diffbot_pid"])
    qtbot.addWidget(panel)
    return panel


def test_no_always_visible_disabled_button_and_no_spin_arrows(qtbot: Any) -> None:
    panel = _panel(qtbot)
    buttons = panel.findChildren(QtWidgets.QAbstractButton)
    shown = [b for b in buttons if b.isVisibleTo(panel)]
    assert shown and all(b.isEnabled() for b in shown), [b.text() for b in shown]
    assert not panel.revert_btn.isVisibleTo(panel)  # nothing edited
    for widget in panel.inputs["Left"].values():
        if isinstance(widget, QtWidgets.QAbstractSpinBox):
            assert widget.buttonSymbols() == QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons
    assert panel.link_all.text() == "L=R" and panel.link_all.isChecked()  # all equal
    assert panel.presets_btn.text() == "Presets ▾"


def test_l_r_box_and_a_single_rows_link(qtbot: Any) -> None:
    panel = _panel(qtbot)
    panel.links["ki"].setChecked(False)  # its label's right-click menu
    assert panel.labels["ki"].text() == "Ki ≠" and not panel.link_all.isChecked()
    panel.inputs["Left"]["ki"].setValue(0.5)
    assert panel.inputs["Right"]["ki"].value() == pytest.approx(0.02)  # unlinked
    panel.link_all.click()  # link every row: the first column wins
    assert panel.links["ki"].isChecked() and panel.labels["ki"].text() == "Ki"
    assert panel.inputs["Right"]["ki"].value() == pytest.approx(0.5)
    panel.link_all.click()
    assert not any(link.isChecked() for link in panel.links.values())


def test_revert_n_shows_only_while_edited(qtbot: Any) -> None:
    panel = _panel(qtbot)
    panel.mark_sent(panel.values())
    panel.inputs["Left"]["kp"].setValue(0.3)  # linked: both columns
    assert panel.revert_btn.isVisibleTo(panel) and panel.revert_btn.text() == "Revert 2"
    panel.revert_btn.click()
    assert not panel.revert_btn.isVisibleTo(panel) and panel.edited() == []


def test_presets_live_in_the_menu(qtbot: Any) -> None:
    panel = _panel(qtbot)
    saved: list[str] = []
    panel.preset_saved.connect(lambda name, _v: saved.append(name))
    panel.inputs["Left"]["kp"].setValue(0.7)
    panel.save_preset("fast")
    assert saved == ["fast"] and panel.presets_btn.text() == "fast ▾"
    texts = [a.text() for a in panel.presets_menu.actions() if not a.isSeparator()]
    assert texts[:2] == ["fast", "Save as…"] and "Delete" in texts
    panel.inputs["Left"]["kp"].setValue(0.1)
    panel.presets_menu.actions()[0].trigger()  # load it
    assert panel.inputs["Left"]["kp"].value() == pytest.approx(0.7)
    delete = next(a for a in panel.presets_menu.actions() if a.text() == "Delete").menu()
    assert delete is not None
    delete.actions()[0].trigger()
    assert panel.preset_names() == [] and panel.presets_btn.text() == "Presets ▾"


def test_send_log_is_plain_lines_and_double_click_resends(qtbot: Any) -> None:
    from ui.panels.command_log import CommandLog, LogEntry

    log = CommandLog()
    qtbot.addWidget(log)
    resent: list[Any] = []
    log.resend_requested.connect(resent.append)
    log.add(LogEntry(None, "20:41:05", "Update Left PID", "not connected"))
    log.add(LogEntry(1, "20:41:07", "Run Test", "ki 0.015 → 0.02", b"\xaa\x55\x11", "pid"))
    assert log.line(0) == "20:41:07  ▲1  Run Test  ki 0.015 → 0.02"
    assert log.line(1) == "20:41:05  ✕  Update Left PID  not connected"
    item = log.list.item(0)
    assert item is not None and "AA 55 11" in item.toolTip()
    refused = log.list.item(1)
    assert refused is not None
    assert refused.foreground().color() == QtGui.QColor("#FF4040")
    log.list.itemDoubleClicked.emit(refused)  # a refused send has no packet
    log.list.itemDoubleClicked.emit(item)
    assert [e.number for e in resent] == [1]
    assert not log.findChildren(QtWidgets.QPushButton)  # no "Send again"


def test_step_metrics_are_now_prev_and_change(qtbot: Any) -> None:
    from core.analysis.step_response import StepMetrics
    from ui.panels.trigger import TriggerPanel

    parent = QtCore.QObject()
    panel = TriggerPanel(parent)
    qtbot.addWidget(panel.results)
    panel.set_signals(StreamConfigLoader(DEFAULT_CONFIG_PATH).get_stream("pid"))
    table = panel.metrics_table
    headers = [table.horizontalHeaderItem(i) for i in range(3)]
    assert [h.text() for h in headers if h is not None] == ["Now", "Prev", "Δ"]
    before = StepMetrics(1.0, 0.0, 0.3, 0.22, 8.0, 0.6, 0.004)
    now = StepMetrics(1.0, 0.0, 0.3, 0.18, 12.0, 0.5, 0.002)
    panel.show_metrics(now, before)
    cell = table.item(0, 0)
    assert cell is not None and cell.foreground().color() == QtGui.QColor("#FFE81A")
    assert panel.metric_text(1, 2) == "▲ +4 pt"  # overshoot grew: amber
    rise_change = table.item(0, 2)
    assert rise_change is not None and rise_change.foreground().color() == QtGui.QColor("#3DFF6E")
