"""
Device profiles in the dashboard (R8.2): the profile menu, switching (only while
disconnected), port and baud per profile, and New profile.

Marked `qt`: skipped when pytest-qt is disabled or Qt can't load (see tests/conftest.py).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6 import QtCore, QtWidgets  # noqa: E402

from core.config import DEFAULT_CONFIG_PATH  # noqa: E402
from core.types import EngineState  # noqa: E402
from ui.app_settings import KEY_CONFIG_PATH, KEY_PROFILES_DIR  # noqa: E402

pytestmark = pytest.mark.qt


@pytest.fixture
def boxes(monkeypatch: Any) -> list[tuple[str, str]]:
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
def folder(tmp_path: Path) -> Path:
    """A profiles folder with an IMU-only profile, `imu-board`, at 9600 baud."""
    doc = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    doc["profile"] = {"name": "imu-board", "format": "binary", "baud": 9600}
    doc["streams"] = {"imu_6axis": doc["streams"]["imu_6axis"]}
    path = tmp_path / "profiles" / "imu-board.json"
    path.parent.mkdir()
    path.write_text(json.dumps(doc, indent=4), encoding="utf-8")
    return path.parent


def _window(qtbot: Any, tmp_path: Path, folder: Path) -> Any:
    from ui.main_window import MainWindow

    settings = QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.Format.IniFormat)
    settings.setValue(KEY_PROFILES_DIR, str(folder))
    win = MainWindow(DEFAULT_CONFIG_PATH, settings=settings)
    qtbot.addWidget(win)
    return win


def _menu_names(win: Any) -> list[str]:
    return [a.text() for a in win.panel.conn_panel.profile_menu.actions() if a.text()]


def test_the_profile_menu_lists_the_bundled_and_folder_profiles(
    qtbot: Any, tmp_path: Path, folder: Path
) -> None:
    win = _window(qtbot, tmp_path, folder)
    conn = win.panel.conn_panel

    assert conn.profile_btn.text() == "diffbot · binary ▾"
    assert _menu_names(win) == [
        "diffbot   ·  binary",
        "imu-board   ·  binary",
        "New profile…",
        "Open profile file…",
        "Edit profile…",
    ]
    checked = [a.text() for a in conn.profile_menu.actions() if a.isChecked()]
    assert checked == ["diffbot   ·  binary"]
    assert win.windowTitle() == "Serial Binary Plotter - diffbot (streams.json)"
    qtbot.waitUntil(lambda: win.engine._profile == {"name": "diffbot", "format": "binary"})
    win.close()


def test_switching_profiles_shows_the_other_devices_streams(
    qtbot: Any, tmp_path: Path, folder: Path
) -> None:
    win = _window(qtbot, tmp_path, folder)
    panel = win.panel
    panel.select_stream("pid_ff")
    imu = folder / "imu-board.json"

    win.panel.conn_panel.profile_actions[str(imu.resolve())].trigger()

    assert win.stream_loader.path == imu
    assert [panel.stream_tabs.itemData(i) for i in range(panel.stream_tabs.count())] == [
        "imu_6axis"
    ]
    assert panel.conn_panel.baud_combo.currentText() == "9600"  # the profile's baud
    assert panel.conn_panel.profile_btn.text() == "imu-board · binary ▾"
    assert win.configurator.filepath == str(imu)
    assert list(win.configurator.drafts) == ["imu_6axis"]
    assert win.windowTitle() == "Serial Binary Plotter - imu-board (imu-board.json)"
    assert win.settings.value(KEY_CONFIG_PATH, type=str) == str(imu)
    qtbot.waitUntil(lambda: set(win.stores.keys()) == {"imu_6axis"}, timeout=5000)
    qtbot.waitUntil(lambda: win.engine._profile["name"] == "imu-board")
    assert win.plot.signal_views and all(
        k.startswith(("acc", "gyro")) for k in win.plot.signal_views
    )

    win.switch_profile(DEFAULT_CONFIG_PATH)  # back: its remembered stream again
    assert panel.current_stream_key() == "pid_ff"
    assert panel.conn_panel.baud_combo.currentText() == "115200"
    win.close()


def test_port_and_baud_are_remembered_per_profile(qtbot: Any, tmp_path: Path, folder: Path) -> None:
    win = _window(qtbot, tmp_path, folder)
    conn = win.panel.conn_panel
    imu = folder / "imu-board.json"
    win.switch_profile(imu)
    conn.baud_combo.setCurrentText("57600")
    conn.port_combo.setCurrentIndex(conn.port_combo.findText("VIRTUAL"))
    conn.connect_btn.click()
    qtbot.waitUntil(lambda: win.engine_state == EngineState.RUNNING, timeout=5000)

    assert not conn.profile_btn.isEnabled()  # no switching under a running session
    assert not win.switch_profile(DEFAULT_CONFIG_PATH)
    assert win.stream_loader.path == imu
    assert win.lbl_status.text() == "Disconnect before switching profiles"

    conn.connect_btn.click()
    qtbot.waitUntil(lambda: win.engine_state != EngineState.RUNNING, timeout=5000)
    assert conn.profile_btn.isEnabled()
    win.switch_profile(DEFAULT_CONFIG_PATH)
    assert conn.baud_combo.currentText() == "115200"
    win.switch_profile(imu)
    assert conn.baud_combo.currentText() == "57600"  # what was used with this profile
    assert conn.port_combo.currentText() == "VIRTUAL"
    win.close()


def test_new_profile_writes_a_file_switches_and_opens_the_editor(
    qtbot: Any, tmp_path: Path, folder: Path, boxes: list[tuple[str, str]]
) -> None:
    win = _window(qtbot, tmp_path, folder)
    dialog = win.profile_dialog()
    qtbot.addWidget(dialog)
    assert not dialog.ok_btn.isEnabled()
    dialog.name_edit.setText("esc 3")
    dialog.baud_combo.setCurrentText("38400")
    dialog.start_combo.setCurrentIndex(dialog.start_combo.findData("empty"))
    assert dialog.path() == folder / "esc-3.json"
    assert dialog.ok_btn.isEnabled()

    assert win.create_profile(dialog.path(), dialog.document())

    saved = json.loads((folder / "esc-3.json").read_text(encoding="utf-8"))
    assert saved == {
        "schema_version": 3,
        "profile": {"name": "esc 3", "format": "binary", "baud": 38400},
        "streams": {},
    }
    assert win.stream_loader.profile.name == "esc 3"
    assert win.panel.stream_tabs.count() == 0 and not win.plot.signal_views
    assert win.config_window.isVisible()
    assert win.panel.conn_panel.baud_combo.currentText() == "38400"
    assert "esc 3   ·  binary" in _menu_names(win)
    assert boxes == []

    copy = win.profile_dialog()
    qtbot.addWidget(copy)
    copy.name_edit.setText("esc 3")  # taken: the file name is made free
    assert copy.path() == folder / "esc-3-2.json"
    win.close()


def test_a_broken_profile_is_reported_and_nothing_changes(
    qtbot: Any, tmp_path: Path, folder: Path, boxes: list[tuple[str, str]]
) -> None:
    win = _window(qtbot, tmp_path, folder)
    broken = tmp_path / "broken.json"
    broken.write_text('{"schema_version": 3, "profile": {"format": "morse"}, "streams": {}}')

    assert not win.switch_profile(broken)

    ((kind, text),) = boxes
    assert kind == "warning" and "morse" in text
    assert win.stream_loader.path == DEFAULT_CONFIG_PATH
    assert win.panel.stream_tabs.count() == 3
    win.close()


def test_a_profile_opened_from_elsewhere_is_remembered_in_the_menu(
    qtbot: Any, tmp_path: Path, folder: Path
) -> None:
    elsewhere = tmp_path / "firmware" / "robot-1.json"
    elsewhere.parent.mkdir()
    doc = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    doc["profile"] = {"name": "robot-1", "format": "binary"}
    elsewhere.write_text(json.dumps(doc), encoding="utf-8")
    win = _window(qtbot, tmp_path, folder)

    assert win.switch_profile(elsewhere)
    win.switch_profile(DEFAULT_CONFIG_PATH)

    assert "robot-1   ·  binary" in _menu_names(win)
    win.close()


def test_a_text_profile_plots_from_virtual_and_counts_unmatched_lines(
    qtbot: Any, tmp_path: Path
) -> None:
    """R8.3: a text profile end to end, with the text counters in the status bar."""
    from ui.main_window import MainWindow

    fixture = Path(__file__).parent / "fixtures" / "text_profile.json"
    settings = QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.Format.IniFormat)
    settings.setValue(KEY_PROFILES_DIR, str(tmp_path))
    win = MainWindow(fixture, settings=settings)
    qtbot.addWidget(win)
    conn = win.panel.conn_panel
    assert conn.profile_btn.text() == "arduino-imu · text ▾"
    assert conn.baud_combo.currentText() == "115200"
    qtbot.waitUntil(lambda: win.engine._profile == {"name": "arduino-imu", "format": "text"})

    conn.port_combo.setCurrentIndex(conn.port_combo.findText("VIRTUAL"))
    conn.connect_btn.click()
    qtbot.waitUntil(lambda: win.engine_state == EngineState.RUNNING, timeout=5000)

    def plotting_imu() -> bool:
        packet = win.plot.last_packet
        return packet is not None and set(packet["signals"]) == {"ax", "ay", "az"}

    qtbot.waitUntil(plotting_imu, timeout=5000)

    # "# sim tick" once a second; the report comes about once a second too.
    def unmatched() -> int:
        found = re.search(r"unmatched (\d+)", win.lbl_link.text())
        return int(found.group(1)) if found else 0

    qtbot.waitUntil(lambda: unmatched() > 0, timeout=5000)
    assert "Lines matching no pattern:" in win.lbl_link.toolTip()
    win.close()
