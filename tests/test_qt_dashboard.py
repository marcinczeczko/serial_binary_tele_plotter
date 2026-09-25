"""
The Phase 6 dashboard (R6.1–R6.5): toolbar, stream tabs, docks, Live mode, presets, the
send log and markers, and the readout in the Signals panel.

Marked `qt`: skipped when pytest-qt is disabled or Qt can't load (see tests/conftest.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6 import QtCore  # noqa: E402

from core.config import DEFAULT_CONFIG_PATH, StreamConfigLoader  # noqa: E402
from core.types import EngineState  # noqa: E402

pytestmark = pytest.mark.qt


def _window(qtbot: Any, tmp_path: Path) -> Any:
    from ui.main_window import MainWindow

    settings = QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.Format.IniFormat)
    win = MainWindow(settings=settings)
    qtbot.addWidget(win)
    return win


def _pid_panel(qtbot: Any) -> Any:
    from ui.panels.command_panel import CommandPanel

    panel = CommandPanel(StreamConfigLoader(DEFAULT_CONFIG_PATH).panels["diffbot_pid"])
    qtbot.addWidget(panel)
    return panel


def test_live_mode_sends_a_column_once_edits_settle(qtbot: Any) -> None:
    from ui.panels.command_panel import LIVE_DEBOUNCE_MS

    panel = _pid_panel(qtbot)
    sent: list[Any] = []
    panel.send_requested.connect(sent.append)
    panel.links["kp"].setChecked(False)
    for value in (0.2, 0.3, 0.4):  # a burst of edits: one send after it settles
        panel.inputs["Left"]["kp"].setValue(value)
    qtbot.wait(LIVE_DEBOUNCE_MS * 2)
    assert sent == []  # Manual mode: nothing is sent by editing

    panel.set_live(True)
    for value in (0.5, 0.6, 0.7):
        panel.inputs["Left"]["kp"].setValue(value)
    qtbot.waitUntil(lambda: len(sent) == 1, timeout=2000)
    qtbot.wait(LIVE_DEBOUNCE_MS * 2)
    (request,) = sent
    assert request.live and request.button.label == "Update Left PID"
    assert request.params["Left"]["kp"] == pytest.approx(0.7)

    panel.inputs["Right"]["ki"].setValue(0.05)  # linked: both columns, one send each
    qtbot.waitUntil(lambda: len(sent) == 3, timeout=2000)
    assert {r.button.label for r in sent[1:]} == {"Update Left PID", "Update Right PID"}

    panel.set_live(False)
    panel.inputs["Left"]["kp"].setValue(0.9)
    qtbot.wait(LIVE_DEBOUNCE_MS * 2)
    assert len(sent) == 3


def test_ctrl_enter_presses_the_main_button_and_presets_round_trip(qtbot: Any) -> None:
    panel = _pid_panel(qtbot)
    sent: list[Any] = []
    saved: list[str] = []
    panel.send_requested.connect(sent.append)
    panel.preset_saved.connect(lambda name, _values: saved.append(name))
    panel.press_default()
    assert sent[-1].button.label == "Run Test (Both Motors)"

    panel.inputs["Left"]["rps"].setValue(1.5)  # linked: Right too
    panel.save_preset("fast")
    panel.inputs["Left"]["rps"].setValue(0.3)
    panel.apply_preset("fast")
    assert panel.inputs["Right"]["rps"].value() == 1.5 and saved == ["fast"]
    panel.delete_preset("fast")
    assert panel.preset_names() == []


def test_stream_tabs_show_activity_and_the_time_button_the_window(
    qtbot: Any, tmp_path: Path
) -> None:
    win = _window(qtbot, tmp_path)
    tabs = win.panel.stream_tabs
    assert [tabs.tabText(i) for i in range(tabs.count())] == [
        "PID Telemetry",
        "PID FF",
        "IMU 6-Axis Raw",
    ]
    tabs.set_activity("pid", 200.4)
    tabs.set_activity("imu_6axis", 0)
    assert tabs.tabText(0) == "PID Telemetry · 200 Hz"
    assert tabs.tabText(2) == "IMU 6-Axis Raw · no data"
    assert win.time_btn.text() == "5.000 ms · 2,000 samples"
    win.panel.time_panel.samples_sb.setValue(5000)
    assert win.time_btn.text() == "5.000 ms · 5,000 samples"

    conn = win.panel.conn_panel
    conn.port_combo.setCurrentIndex(conn.port_combo.findText("VIRTUAL"))
    conn.connect_btn.click()
    qtbot.waitUntil(lambda: win.engine_state == EngineState.RUNNING, timeout=5000)
    # The simulator sends the shown stream only; pid_ff shares its frames (R2.2).
    qtbot.waitUntil(lambda: tabs.tabText(0).endswith(" Hz"), timeout=5000)
    qtbot.waitUntil(lambda: tabs.tabText(2).endswith("no data"), timeout=5000)
    conn.connect_btn.click()
    qtbot.waitUntil(lambda: win.engine_state == EngineState.CONFIGURED, timeout=5000)
    assert tabs.tabText(0) == "PID Telemetry · no data"


def test_readout_goes_to_the_signals_panel_and_markers_follow_the_stream(
    qtbot: Any, tmp_path: Path
) -> None:
    win = _window(qtbot, tmp_path)
    conn = win.panel.conn_panel
    conn.port_combo.setCurrentIndex(conn.port_combo.findText("VIRTUAL"))
    conn.connect_btn.click()
    qtbot.waitUntil(lambda: win.engine_state == EngineState.RUNNING, timeout=5000)
    pid = win.stores.get("pid")
    qtbot.waitUntil(lambda: pid is not None and len(pid) > 200, timeout=5000)

    win.panel.conn_panel.pause_btn.click()  # analysis: the readout of the frozen data
    t = win.plot.analysis_packet["time"]
    win.plot.move_cursor(float(t[100]))
    sig = win.panel.sig_panel
    assert sig.cursor_lbl.text() == f"@ {float(t[100]):.3f} s"
    assert sig.value_text("left_measurement") not in ("", "n/a")
    win.panel.conn_panel.pause_btn.click()
    assert sig.cursor_lbl.text() == ""  # live again: no frozen readout

    panel = win.panel.control_panels["diffbot_pid"]
    panel.buttons[0].click()
    entry = win.command_log.entries()[0]
    assert entry.number == 1 and win.plot.marker_count() == 1
    win.command_log.resend_requested.emit(entry)
    again = win.command_log.entries()[0]
    assert again.number == 2 and again.packet == entry.packet
    assert again.detail == "again (as ▲ 1)"
    win.panel.select_stream("imu_6axis")  # no data in this stream: no markers there
    assert win.plot.marker_count() == 0
    win.panel.select_stream("pid")
    assert win.plot.marker_count() == 2

    conn.connect_btn.click()
    qtbot.waitUntil(lambda: win.engine_state == EngineState.CONFIGURED, timeout=5000)
    conn.connect_btn.click()  # a new session: new stream time, old markers dropped
    qtbot.waitUntil(lambda: win.engine_state == EngineState.RUNNING, timeout=5000)
    assert win.plot.marker_count() == 0
    conn.connect_btn.click()
    qtbot.waitUntil(lambda: win.engine_state == EngineState.CONFIGURED, timeout=5000)


def test_configuration_opens_in_its_own_window(qtbot: Any, tmp_path: Path) -> None:
    win = _window(qtbot, tmp_path)
    assert not win.config_window.isVisible()
    win.act_edit_config.trigger()
    assert win.config_window.isVisible()
    assert win.configurator.parent() is win.config_window
    win.config_window.close()


def _mouse(widget: Any, kind: Any, x: float, modifiers: Any = None) -> None:
    from PyQt6 import QtGui, QtWidgets

    left = QtCore.Qt.MouseButton.LeftButton
    event = QtGui.QMouseEvent(
        kind,
        QtCore.QPointF(x, 5),
        QtCore.QPointF(widget.mapToGlobal(QtCore.QPoint(int(x), 5))),
        left,
        left if kind != QtCore.QEvent.Type.MouseButtonRelease else QtCore.Qt.MouseButton.NoButton,
        modifiers or QtCore.Qt.KeyboardModifier.NoModifier,
    )
    QtWidgets.QApplication.sendEvent(widget, event)


def test_dragging_a_parameter_label_scrubs_its_row(qtbot: Any) -> None:
    from ui.panels.command_panel import SCRUB_PX_PER_STEP, ScrubLabel

    panel = _pid_panel(qtbot)
    move = QtCore.QEvent.Type.MouseMove
    label = panel.labels["kp"]
    assert isinstance(label, ScrubLabel) and not isinstance(panel.labels["use_pi"], ScrubLabel)
    panel.mark_sent({"Left": {"kp": 0.1}, "Right": {"kp": 0.1}})

    _mouse(label, QtCore.QEvent.Type.MouseButtonPress, 5)
    _mouse(label, move, 5 + 10 * SCRUB_PX_PER_STEP)  # 10 steps of 0.01, linked: both
    assert panel.inputs["Left"]["kp"].value() == pytest.approx(0.2)
    assert panel.inputs["Right"]["kp"].value() == pytest.approx(0.2)
    assert panel.edited() == [("Left", "kp"), ("Right", "kp")]
    _mouse(label, move, 5 + 10 * SCRUB_PX_PER_STEP, QtCore.Qt.KeyboardModifier.ShiftModifier)
    assert panel.inputs["Left"]["kp"].value() == pytest.approx(1.1)  # ×10
    _mouse(label, move, 5)  # back to where the drag started: exactly the start value
    assert panel.inputs["Left"]["kp"].value() == pytest.approx(0.1)
    _mouse(label, QtCore.QEvent.Type.MouseButtonRelease, 5)
    assert panel.edited() == []

    panel.links["kp"].setChecked(False)
    panel.inputs["Right"]["kp"].setValue(0.5)
    _mouse(label, QtCore.QEvent.Type.MouseButtonPress, 50)
    _mouse(label, move, 50 - 2 * SCRUB_PX_PER_STEP)  # unlinked: each column moves
    assert panel.inputs["Left"]["kp"].value() == pytest.approx(0.08)
    assert panel.inputs["Right"]["kp"].value() == pytest.approx(0.48)
    _mouse(label, move, 50 + 10 * SCRUB_PX_PER_STEP, QtCore.Qt.KeyboardModifier.AltModifier)
    assert panel.inputs["Left"]["kp"].value() == pytest.approx(0.11)  # ×0.1: one step
    _mouse(label, QtCore.QEvent.Type.MouseButtonRelease, 50)


def test_escape_reverts_the_values_edited_since_the_last_send(qtbot: Any) -> None:
    panel = _pid_panel(qtbot)
    panel.show()
    qtbot.waitExposed(panel)
    panel.activateWindow()
    panel.mark_sent({"Left": {"kp": 0.1, "rps": 0.3}, "Right": {"kp": 0.1, "rps": 0.3}})
    field = panel.inputs["Left"]["kp"]
    field.setValue(0.4)
    panel.inputs["Right"]["rps"].setValue(2.0)
    assert len(panel.edited()) == 4  # linked rows: both columns
    field.setFocus()
    qtbot.waitUntil(field.hasFocus, timeout=2000)
    qtbot.keyClick(field, QtCore.Qt.Key.Key_Escape)
    assert panel.edited() == []
    assert field.value() == pytest.approx(0.1)
    assert panel.inputs["Right"]["rps"].value() == pytest.approx(0.3)


def test_dropping_a_signal_on_another_lane_moves_it(qtbot: Any) -> None:
    from PyQt6 import QtGui

    from ui.panels.signals import SignalListPanel

    panel = SignalListPanel()
    qtbot.addWidget(panel)
    panel.resize(320, 900)
    panel.rebuild_list(StreamConfigLoader(DEFAULT_CONFIG_PATH).get_stream("pid"))
    panel.show()
    qtbot.waitExposed(panel)
    tree = panel.tree
    moves: list[tuple[str, str, str]] = []
    panel.signal_lane_changed.connect(lambda *a: moves.append(a))
    (speed, speed_label), (error, _), *_ = panel.lanes()

    def drop(sid: str, pos: QtCore.QPoint) -> None:
        tree.setCurrentItem(panel._items[sid])
        event = QtGui.QDropEvent(
            QtCore.QPointF(pos),
            QtCore.Qt.DropAction.MoveAction,
            QtCore.QMimeData(),
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        tree.dropEvent(event)

    drop("left_error", tree.visualItemRect(panel._lane_items[speed]).center())
    qtbot.waitUntil(lambda: panel.lane_of("left_error") == speed, timeout=2000)
    assert moves[-1] == ("left_error", speed, speed_label)

    # Onto a signal row: that signal's lane.
    drop("left_error", tree.visualItemRect(panel._items["right_error"]).center())
    qtbot.waitUntil(lambda: panel.lane_of("left_error") == error, timeout=2000)

    # Below the list: a new lane (lanes collapsed, so there's empty space whatever the font).
    tree.collapseAll()
    viewport = tree.viewport()
    assert viewport is not None
    drop("left_error", QtCore.QPoint(20, viewport.height() - 5))
    qtbot.waitUntil(lambda: (panel.lane_of("left_error") or "").startswith("Lane "), timeout=2000)
    assert moves[-1][1] == moves[-1][2] == panel.lane_of("left_error")
