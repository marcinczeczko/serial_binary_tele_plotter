"""
Generated control panels (R5.2), remembered UI state (R5.3) and saving a migrated
document from the Configuration tab (R5.1), in the real UI.

Marked `qt`: skipped when pytest-qt is disabled or Qt can't load (see tests/conftest.py).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6 import QtCore, QtWidgets  # noqa: E402

from core.config import DEFAULT_CONFIG_PATH  # noqa: E402
from core.types import EngineState  # noqa: E402

pytestmark = pytest.mark.qt

V1_FIXTURE = Path(__file__).parent / "fixtures" / "streams_v1.json"


def _window(qtbot: Any, settings_file: Path, config: Path = DEFAULT_CONFIG_PATH) -> Any:
    from ui.main_window import MainWindow

    settings = QtCore.QSettings(str(settings_file), QtCore.QSettings.Format.IniFormat)
    win = MainWindow(config, settings=settings)
    qtbot.addWidget(win)
    return win


def _connect_virtual(qtbot: Any, win: Any) -> None:
    conn = win.panel.conn_panel
    conn.port_combo.setCurrentIndex(conn.port_combo.findText("VIRTUAL"))
    conn.connect_btn.click()
    qtbot.waitUntil(lambda: win.engine_state == EngineState.RUNNING, timeout=5000)


def _disconnect(qtbot: Any, win: Any) -> None:
    win.panel.conn_panel.connect_btn.click()
    qtbot.waitUntil(lambda: win.engine_state == EngineState.CONFIGURED, timeout=5000)


def _show(win: Any, stream: str) -> None:
    combo = win.panel.payload_combo
    combo.setCurrentIndex(combo.findData(stream))


def test_generated_pid_panel_sends_config_defined_commands(qtbot: Any, tmp_path: Path) -> None:
    win = _window(qtbot, tmp_path / "settings.ini")
    win.show()
    _show(win, "pid")
    panel = win.panel.control_panels["diffbot_pid"]
    section = win.panel.control_sections["diffbot_pid"]
    assert win.panel.dynamic_stack.currentWidget() is section
    assert not win.panel.dynamic_stack.isHidden()
    assert list(panel.inputs) == ["Left", "Right"]
    assert list(panel.inputs["Left"]) == [
        *("kp", "ki", "k1", "k2", "k3", "k_aw", "alpha", "rps", "use_ramp", "use_pi")
    ]
    assert isinstance(panel.inputs["Left"]["use_pi"], QtWidgets.QCheckBox)
    assert panel.inputs["Left"]["ki"].decimals() == 5  # C8: fine enough for small Ki
    assert panel.inputs["Right"]["rps"].minimum() == -50.0  # C8: reverse is allowed
    labels = [b.text() for b in panel.buttons]
    assert labels == ["Update Left PID", "Update Right PID", "Run Test (Both Motors)"]

    panel.buttons[0].click()
    assert win.lbl_status.text() == "Not connected: 'Update Left PID' not sent"

    _connect_virtual(qtbot, win)
    panel.inputs["Left"]["kp"].setValue(2.5)
    panel.inputs["Left"]["use_pi"].setChecked(True)
    panel.buttons[0].click()
    assert win.lbl_status.text() == ("Sent 'Update Left PID': PID gains, one motor (ID 0x10, 41 B)")

    def left_kp() -> float:
        sim = win.engine._sim  # test only: the simulator decoded what the panel sent
        return sim.synth.model.gains("left").kp if sim is not None else 0.0

    qtbot.waitUntil(lambda: left_kp() == pytest.approx(2.5), timeout=3000)

    panel.inputs["Right"]["rps"].setValue(-1.25)
    panel.buttons[2].click()  # both motors, one packet
    assert "(ID 0x11, 74 B)" in win.lbl_status.text()
    qtbot.waitUntil(
        lambda: win.engine._sim.synth.model.gains("right").rps == pytest.approx(-1.25),
        timeout=3000,
    )
    _show(win, "imu_6axis")
    assert win.panel.dynamic_stack.isHidden()  # the IMU stream names no panel
    _disconnect(qtbot, win)


def test_a_value_that_does_not_fit_is_refused(qtbot: Any, tmp_path: Path) -> None:
    doc = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    doc["commands"]["mode"] = {
        "label": "Mode",
        "packet_id": 0x20,
        "fields": [{"name": "mode", "type": "u8", "param": "mode"}],
    }
    doc["panels"]["modes"] = {
        "title": "Modes",
        "parameters": {"mode": {"label": "Mode", "default": 1.5, "min": 0, "max": 400}},
        "buttons": [{"label": "Set mode", "command": "mode"}],
    }
    doc["streams"]["imu_6axis"]["controls"] = "modes"
    config = tmp_path / "streams.json"
    config.write_text(json.dumps(doc), encoding="utf-8")
    win = _window(qtbot, tmp_path / "settings.ini", config)
    _show(win, "imu_6axis")
    _connect_virtual(qtbot, win)
    panel = win.panel.control_panels["modes"]

    panel.buttons[0].click()
    assert win.lbl_status.text() == "Not sent: mode.mode: u8 needs a whole number, got 1.5"
    panel.inputs[""]["mode"].setValue(300)
    panel.buttons[0].click()
    assert win.lbl_status.text() == "Not sent: mode.mode: 300 is outside u8 (0..255)"
    panel.inputs[""]["mode"].setValue(7)
    panel.buttons[0].click()
    assert win.lbl_status.text().startswith("Sent 'Set mode'")
    _disconnect(qtbot, win)


def test_ui_state_is_remembered_between_runs(qtbot: Any, tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.ini"
    win = _window(qtbot, settings_file)
    conn = win.panel.conn_panel
    conn.baud_combo.setCurrentText("460800")
    _connect_virtual(qtbot, win)
    _disconnect(qtbot, win)
    _show(win, "pid_ff")
    hidden, moved = list(win.panel.get_current_stream_config()["signals"])[:2]
    win.panel.sig_panel.rows[hidden].enable_checkbox.setChecked(False)
    lane_combo = win.panel.sig_panel.rows[moved].lane_combo
    lane_combo.setCurrentIndex(lane_combo.count() - 1)  # "New lane"
    new_lane = win.panel.sig_panel.lane_of(moved)
    assert new_lane is not None and new_lane.startswith("Lane ")
    win.panel.control_panels["diffbot_pid"].inputs["Right"]["rps"].setValue(-1.25)
    win.panel.control_panels["diffbot_pid"].inputs["Left"]["use_ramp"].setChecked(True)
    win.close()
    win.settings.sync()

    again = _window(qtbot, settings_file)

    panel = again.panel
    assert panel.current_stream_key() == "pid_ff"
    assert panel.conn_panel.port_combo.currentText() == "VIRTUAL"
    assert panel.conn_panel.baud_combo.currentText() == "460800"
    assert not panel.sig_panel.rows[hidden].enable_checkbox.isChecked()
    assert not again.plot.signal_views[hidden]["visible"]
    assert panel.sig_panel.lane_of(moved) == new_lane
    assert again.plot.signal_views[moved]["lane"] == new_lane
    pid = panel.control_panels["diffbot_pid"]
    assert pid.inputs["Right"]["rps"].value() == -1.25
    assert pid.inputs["Left"]["use_ramp"].isChecked()
    assert pid.inputs["Left"]["rps"].value() == 0.3  # untouched values keep their default

    again.act_reset_view.trigger()  # back to streams.json for this stream
    assert panel.sig_panel.rows[hidden].enable_checkbox.isChecked()
    assert again.plot.signal_views[moved]["lane"] != new_lane
    assert panel.current_stream_key() == "pid_ff"
    assert pid.inputs["Right"]["rps"].value() == -1.25  # panel values aren't view state
    again.close()


def test_a_remembered_port_that_is_gone_is_not_invented(qtbot: Any, tmp_path: Path) -> None:
    settings = QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.Format.IniFormat)
    settings.setValue("connection/port", "/dev/ttyGONE")
    settings.setValue("connection/baud", "1000000")
    settings.sync()
    win = _window(qtbot, tmp_path / "s.ini")
    assert win.panel.conn_panel.port_combo.findText("/dev/ttyGONE") < 0
    assert win.panel.conn_panel.baud_combo.currentText() == "1000000"


def test_config_tab_saves_a_schema_1_file_as_schema_2(
    qtbot: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(QtWidgets.QMessageBox, kind, staticmethod(lambda *a, **k: 0))
    config = tmp_path / "streams.json"
    config.write_bytes(V1_FIXTURE.read_bytes())
    win = _window(qtbot, tmp_path / "settings.ini", config)
    assert "schema 1 read as 2" in win.lbl_status.text()
    assert "panel_type 'pid' -> controls" in win.lbl_status.toolTip()

    tab = win.configurator
    tab.stream_list.setCurrentRow(0)  # pid
    combo = tab.editor.panel_combo
    assert [combo.itemText(i) for i in range(combo.count())] == ["(none)", "diffbot_pid"]
    assert combo.currentData() == "diffbot_pid"
    tab.stream_list.setCurrentRow(1)  # pid_ff: no controls from now on
    combo.setCurrentIndex(combo.findData(None))
    tab.stream_list.setCurrentRow(0)
    tab.save_to_file()

    saved = json.loads(config.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 2 and list(saved)[:3] == [
        "schema_version",
        "commands",
        "panels",
    ]
    assert saved["streams"]["pid"]["controls"] == "diffbot_pid"
    assert "controls" not in saved["streams"]["pid_ff"]
    assert (tmp_path / "streams.json.bak").read_bytes() == V1_FIXTURE.read_bytes()
    assert win.panel.stream_loader.source_version == 2  # reloaded after saving
    _show(win, "pid")
    assert not win.panel.dynamic_stack.isHidden()
    _show(win, "pid_ff")
    assert win.panel.dynamic_stack.isHidden()
    win.close()
