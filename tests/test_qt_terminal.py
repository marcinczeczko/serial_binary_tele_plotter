"""
The terminal a text profile sends from (R8.5): typing, history, line endings, replies.

Marked `qt`: skipped when pytest-qt is disabled or Qt can't load (see tests/conftest.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6 import QtCore  # noqa: E402

from core.types import EngineState  # noqa: E402

pytestmark = pytest.mark.qt

FIXTURE = Path(__file__).parent / "fixtures" / "text_profile.json"


def _terminal(qtbot: Any) -> Any:
    from ui.panels.terminal import Terminal

    terminal = Terminal()
    qtbot.addWidget(terminal)
    terminal.set_connected(True)
    return terminal


def test_enter_sends_the_line_and_clears_it(qtbot: Any) -> None:
    terminal = _terminal(qtbot)
    sent: list[str] = []
    terminal.line_entered.connect(sent.append)
    qtbot.keyClicks(terminal.input, "PID 0 0.25")
    qtbot.keyClick(terminal.input, QtCore.Qt.Key.Key_Return)
    qtbot.keyClick(terminal.input, QtCore.Qt.Key.Key_Return)  # empty: nothing
    assert sent == ["PID 0 0.25"]
    assert terminal.input.text() == ""


def test_up_and_down_walk_the_history_and_esc_clears(qtbot: Any) -> None:
    terminal = _terminal(qtbot)
    for line in ("A", "B"):
        qtbot.keyClicks(terminal.input, line)
        qtbot.keyClick(terminal.input, QtCore.Qt.Key.Key_Return)
    qtbot.keyClicks(terminal.input, "draft")
    qtbot.keyClick(terminal.input, QtCore.Qt.Key.Key_Up)
    assert terminal.input.text() == "B"
    qtbot.keyClick(terminal.input, QtCore.Qt.Key.Key_Up)
    qtbot.keyClick(terminal.input, QtCore.Qt.Key.Key_Up)
    assert terminal.input.text() == "A"
    qtbot.keyClick(terminal.input, QtCore.Qt.Key.Key_Down)
    qtbot.keyClick(terminal.input, QtCore.Qt.Key.Key_Down)
    assert terminal.input.text() == "draft"  # back to what was being typed
    qtbot.keyClick(terminal.input, QtCore.Qt.Key.Key_Escape)
    assert terminal.input.text() == ""


def test_endings_and_the_disconnected_state(qtbot: Any) -> None:
    terminal = _terminal(qtbot)
    assert terminal.ending == b"\n"
    for name, ending in (("CR LF", b"\r\n"), ("CR", b"\r"), ("none", b""), ("nope", b"\n")):
        terminal.set_ending(name)
        assert terminal.ending == ending
    terminal.set_connected(False)
    assert not terminal.input.isEnabled()
    assert terminal.input.placeholderText() == "Connect to send"


def test_the_transcript_shows_sends_replies_and_refusals(qtbot: Any) -> None:
    terminal = _terminal(qtbot)
    terminal.add_sent(3, "12:04:11", "PID 0 0.25")
    terminal.add_replies("12:04:12", ["ok <kp>"], dropped=2)
    terminal.add_refused("12:04:13", "Ω", "only ASCII can be sent")
    rows = terminal.rows()
    assert rows[0].split() == ["▲", "3", "12:04:11", "PID", "0", "0.25"]
    assert rows[1].endswith("… 2 more lines not shown") and rows[2].endswith("ok <kp>")
    assert rows[3].endswith("Ω (only ASCII can be sent)")


def _window(qtbot: Any, tmp_path: Path) -> Any:
    from ui.main_window import MainWindow

    settings = QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.Format.IniFormat)
    win = MainWindow(FIXTURE, settings=settings)
    qtbot.addWidget(win)
    return win


def test_a_text_profile_sends_from_the_terminal_and_shows_the_reply(
    qtbot: Any, tmp_path: Path
) -> None:
    win = _window(qtbot, tmp_path)
    terminal = win.panel.terminal
    assert win.panel.controls_stack.currentWidget() is terminal
    assert win.controls_dock.windowTitle() == "Terminal"
    assert not win.command_log.isVisibleTo(win)
    assert not terminal.input.isEnabled()

    conn = win.panel.conn_panel
    conn.port_combo.setCurrentIndex(conn.port_combo.findText("VIRTUAL"))
    conn.connect_btn.click()
    qtbot.waitUntil(lambda: win.engine_state == EngineState.RUNNING, timeout=5000)
    assert terminal.input.isEnabled()
    qtbot.waitUntil(lambda: win.stores.get("imu").latest_time_s() is not None, timeout=5000)

    terminal.input.setText("PID 0 0.25")
    terminal.input.returnPressed.emit()
    assert terminal.rows()[-1].split()[:2] == ["▲", "1"]
    assert win.lbl_status.text() == "Sent ▲ 1: PID 0 0.25"
    assert any("▲ 1 PID 0 0.25" in label for _t, label in win._markers["imu"])
    # The simulator answers; the reply comes with the next link report (~1 s).
    qtbot.waitUntil(lambda: any("ok: PID 0 0.25" in r for r in terminal.rows()), timeout=5000)
    assert not any(r.endswith(",") or "IMU," in r for r in terminal.rows())  # no telemetry

    terminal.input.setText("kp=½")
    terminal.input.returnPressed.emit()
    assert terminal.rows()[-1].endswith("(only ASCII can be sent)")
    assert win.lbl_status.text() == "Not sent: only ASCII can be sent"
    win.close()


def test_the_line_ending_is_remembered_per_profile(qtbot: Any, tmp_path: Path) -> None:
    win = _window(qtbot, tmp_path)
    combo = win.panel.terminal.ending_combo
    combo.setCurrentText("CR LF")
    combo.activated.emit(combo.currentIndex())
    win.close()

    again = _window(qtbot, tmp_path)
    assert again.panel.terminal.ending_combo.currentText() == "CR LF"
    again.close()


def test_a_binary_profile_keeps_its_panels(qtbot: Any, tmp_path: Path) -> None:
    from core.config import DEFAULT_CONFIG_PATH
    from ui.main_window import MainWindow

    settings = QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.Format.IniFormat)
    win = MainWindow(DEFAULT_CONFIG_PATH, settings=settings)
    qtbot.addWidget(win)
    win.panel.select_stream("pid")
    assert win.panel.controls_stack.currentWidget() is not win.panel.terminal
    assert win.command_log.isVisibleTo(win)
    win.close()
