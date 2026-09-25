"""
The top bar (R9.2, ADR-0012 decision 1): one row in place of the toolbar, the stream-tabs
row and the status bar.

Marked `qt`: skipped when pytest-qt is disabled or Qt can't load (see tests/conftest.py).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6 import QtCore, QtWidgets  # noqa: E402

pytestmark = pytest.mark.qt


def _window(qtbot: Any, tmp_path: Path) -> Any:
    from ui.main_window import MainWindow

    settings = QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.Format.IniFormat)
    win = MainWindow(settings=settings)
    qtbot.addWidget(win)
    return win


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(10.0, "10.0 s"), (2.5, "2.50 s"), (250.0, "250 s"), (0.5, "500 ms"), (2e-4, "200 µs")],
)
def test_format_duration(seconds: float, text: str) -> None:
    from ui.panels.top_bar import format_duration

    assert format_duration(seconds) == text


@pytest.mark.parametrize(
    ("n", "text"), [(500, "500"), (2000, "2.00k"), (100_000, "100k"), (2_500_000, "2.50M")]
)
def test_format_count(n: int, text: str) -> None:
    from ui.panels.top_bar import format_count

    assert format_count(n) == text


def test_one_bar_replaces_toolbar_tabs_row_and_status_bar(qtbot: Any, tmp_path: Path) -> None:
    win = _window(qtbot, tmp_path)
    assert win.findChildren(QtWidgets.QToolBar) == []
    assert win.findChildren(QtWidgets.QStatusBar) == []
    assert win.findChildren(QtWidgets.QDockWidget) == []
    bar = win.top_bar
    assert bar.height() == 36
    conn = win.panel.conn_panel
    for widget in (
        conn.profile_btn,
        conn.port_combo,
        conn.connect_btn,
        conn.pause_btn,
        win.panel.stream_tabs,
        win.lbl_status,
        win.time_btn,
        win.rate_points,
        win.trigger_btn,
        win.record_btn,
        win.lbl_link,
    ):
        assert bar.isAncestorOf(widget), widget
    assert not hasattr(conn, "refresh_btn")  # the port list refreshes when it opens
    assert win.trigger_btn.text() == "T —" and win.record_btn.text() == "REC"
    assert not win.record_btn.isEnabled()  # nothing to record while disconnected
    assert conn.pause_btn.text() == "—"


def test_baud_only_for_a_serial_port_and_ports_refresh_on_open(
    qtbot: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    from serial.tools import list_ports

    win = _window(qtbot, tmp_path)
    conn = win.panel.conn_panel
    conn.port_combo.setCurrentIndex(conn.port_combo.findText("VIRTUAL"))
    assert conn.baud_combo.isHidden()
    monkeypatch.setattr(list_ports, "comports", lambda: [SimpleNamespace(device="/dev/ttyFAKE0")])
    conn.port_combo.showPopup()  # opening the list re-reads the ports
    conn.port_combo.hidePopup()
    assert conn.port_combo.findText("/dev/ttyFAKE0") >= 0
    assert conn.port_combo.currentText() == "VIRTUAL"  # the choice is kept
    conn.port_combo.setCurrentText("/dev/ttyFAKE0")
    assert not conn.baud_combo.isHidden()


def test_run_box_shows_the_view_state(qtbot: Any) -> None:
    from ui.panels.top_bar import RunBox

    box = RunBox()
    qtbot.addWidget(box)
    assert box.text() == "—"
    box.set_connected(True)
    assert box.text() == "RUN"
    box.click()
    assert box.isChecked() and box.text() == "STOP"
    box.set_connected(False)
    assert box.text() == "STOP"  # a frozen view stays frozen after a disconnect


def test_link_health_shows_only_problems_in_amber(qtbot: Any) -> None:
    from ui.panels.top_bar import LinkHealth

    health = LinkHealth()
    qtbot.addWidget(health)
    health.show_report("29 kB/s", "", "Bytes received: 10")
    assert health.text() == "29 kB/s" and "Bytes received: 10" in health.toolTip()
    health.show_report("29 kB/s", "CRC 3  LOST 12", "Header CRC errors: 3")
    assert health.text() == "CRC 3  LOST 12" and "#FFB000" in health.styleSheet()
    assert health.toolTip().startswith("29 kB/s")


def test_messages_fade_but_errors_stay(qtbot: Any, monkeypatch: Any) -> None:
    from ui.panels import top_bar

    monkeypatch.setattr(top_bar, "TRANSIENT_MS", 30)
    label = top_bar.MessageLabel()
    qtbot.addWidget(label)
    label.say("Sent PID L+R")
    assert label.text() == "Sent PID L+R"
    qtbot.waitUntil(lambda: label.text() == "", timeout=1000)
    label.say("Could not open /dev/ttyUSB0", "error")
    qtbot.wait(100)
    assert label.text() == "Could not open /dev/ttyUSB0" and "#FF4040" in label.styleSheet()
    label.say("Next")  # the next message replaces it
    assert label.level == "info"
