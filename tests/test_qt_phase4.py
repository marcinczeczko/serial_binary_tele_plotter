"""
Recording, replay, export and trigger capture in the real UI (R4.1-R4.5).

Marked `qt`: skipped when pytest-qt is disabled or Qt can't load (see tests/conftest.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6 import QtCore  # noqa: E402

from core.recording.sbtp import RecordingReader  # noqa: E402
from core.types import EngineState  # noqa: E402

pytestmark = pytest.mark.qt


def _window(qtbot: Any, tmp_path: Path, record_on_connect: bool = False) -> Any:
    from ui.app_settings import KEY_RECORD_ON_CONNECT, KEY_RECORDINGS_DIR
    from ui.main_window import MainWindow

    settings = QtCore.QSettings(str(tmp_path / "settings.ini"), QtCore.QSettings.Format.IniFormat)
    settings.setValue(KEY_RECORD_ON_CONNECT, record_on_connect)
    settings.setValue(KEY_RECORDINGS_DIR, str(tmp_path / "recordings"))
    win = MainWindow(settings=settings)
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


def test_trigger_controller_captures_around_a_crossing(qtbot: Any) -> None:
    from core.acquisition.storage import SampleStore
    from core.acquisition.timebase import TimeBaseConfig
    from core.analysis.trigger import TriggerSpec
    from ui.charts.trigger_controller import TriggerController

    store = SampleStore(10_000)
    store.configure({"sp": {"field": "sp"}, "m": {"field": "m"}}, TimeBaseConfig(scale_s=0.01))
    store.append({"loop_cntr": i, "sp": 0.0, "m": 0.0} for i in range(100))
    controller = TriggerController()
    controller.set_store(store)
    captures: list[tuple[Any, float, str]] = []
    states: list[str] = []
    controller.captured.connect(lambda *a: captures.append(a))
    controller.state_changed.connect(states.append)

    controller.arm(TriggerSpec("sp", level=0.5, edge="rising", pre_s=0.2, post_s=0.3))
    store.append({"loop_cntr": i, "sp": 1.0, "m": 0.5} for i in range(100, 120))
    controller.poll()
    assert states == ["armed", "fired"] and not captures  # not 0.3 s after yet
    store.append({"loop_cntr": i, "sp": 1.0, "m": 0.9} for i in range(120, 140))
    controller.poll()

    ((packet, t_trig, note),) = captures
    assert t_trig == pytest.approx(0.995)  # between 0.99 (0) and 1.00 (1)
    assert packet["time"][0] == pytest.approx(0.80) and packet["time"][-1] == pytest.approx(1.29)
    assert set(packet["signals"]) == {"sp", "m"} and note == ""
    assert states[-1] == "idle" and not controller.armed


def test_record_on_connect_then_replay_from_the_menu(qtbot: Any, tmp_path: Path) -> None:
    win = _window(qtbot, tmp_path, record_on_connect=True)
    assert win.act_record_on_connect.isChecked()
    _connect_virtual(qtbot, win)
    qtbot.waitUntil(lambda: win.record_btn.text().startswith("REC 0"), timeout=5000)
    assert win.act_record.isChecked()
    pid = win.stores.get("pid")
    qtbot.waitUntil(lambda: pid is not None and pid.total_stored >= 100, timeout=5000)
    _disconnect(qtbot, win)
    qtbot.waitUntil(lambda: win.record_btn.text() == "REC", timeout=5000)

    (path,) = (tmp_path / "recordings").glob("*.sbtp")
    reader = RecordingReader(path)
    assert reader.header.source == "VIRTUAL" and "pid" in reader.header.streams
    recorded = sum(len(data) for _, data in reader.chunks())
    frame_len = 146  # the bundled pid frame: 5 B header + 140 B payload + CRC
    assert recorded % frame_len == 0 and recorded // frame_len >= 50

    win.speed_actions[0.0].trigger()  # max speed
    win.start_replay(str(path))
    qtbot.waitUntil(lambda: win.lbl_status.text().startswith("Replay finished"), timeout=10000)
    replayed = win.stores.get("pid")
    assert replayed is not None and len(replayed) == recorded // frame_len
    assert win.engine_state == EngineState.CONFIGURED
    assert not win.panel.conn_panel.connect_btn.isChecked()
    assert win.record_btn.text() == "REC"  # a replay isn't recorded automatically
    assert len(list((tmp_path / "recordings").glob("*.sbtp"))) == 1


def test_export_window_selection_and_all_streams(qtbot: Any, tmp_path: Path) -> None:
    win = _window(qtbot, tmp_path)
    _connect_virtual(qtbot, win)
    pid = win.stores.get("pid")
    qtbot.waitUntil(lambda: pid is not None and pid.total_stored >= 300, timeout=5000)
    _disconnect(qtbot, win)

    rows = win.export_shown(tmp_path / "pid.csv")
    lines = (tmp_path / "pid.csv").read_text().splitlines()
    assert rows == len(lines) - 1 >= 300
    assert lines[0].split(",")[:2] == ["time_s", "left_target_setpoint"]

    win.panel.conn_panel.pause_btn.setChecked(True)  # analysis: export what's in view
    t = win.plot.analysis_packet["time"]
    win.plot.plot.setXRange(float(t[10]), float(t[59]), padding=0)
    assert win.export_shown(tmp_path / "view.csv") == 50
    assert "Exported 50 rows" in win.lbl_status.text()

    files = win.export_all(tmp_path / "all.csv")
    names = {p.name for p in files}
    assert {"all_pid.csv", "all_pid_ff.csv"} <= names  # pid_ff shares pid's frames
    assert "all_imu_6axis.csv" not in names  # no data for it: nothing written


def test_trigger_capture_pauses_with_metrics(qtbot: Any, tmp_path: Path) -> None:
    win = _window(qtbot, tmp_path)
    panel = win.panel.trigger_panel
    assert panel.signal_combo.currentData() == "left_target_setpoint"
    assert panel.measurement_combo.currentData() == "left_measurement"
    _connect_virtual(qtbot, win)

    # The simulated target drops from +0.3 to 0 at t = 2 s.
    panel.edge_combo.setCurrentText("falling")
    panel.level_sb.setValue(0.15)
    panel.pre_sb.setValue(0.5)
    panel.post_sb.setValue(0.4)
    panel.arm_btn.click()
    assert panel.state_lbl.text() == "Armed: waiting…"
    # On the plot and in the top bar while armed (R6.5, R9.2).
    assert win.trigger_btn.text() == "T ╲ 0.15 ARMED"
    assert win.trigger_btn.toolTip() == "↘ L: Target Setpoint < 0.15 · ARMED"
    line = win.plot.trigger_line()
    assert line is not None and line.value() == pytest.approx(0.15)
    line.setValue(0.1)  # dragging the line sets the level
    line.sigPositionChangeFinished.emit(line)
    assert panel.level_sb.value() == pytest.approx(0.1)
    line.setValue(0.15)
    line.sigPositionChangeFinished.emit(line)
    qtbot.waitUntil(lambda: win.plot.analysis_packet is not None, timeout=8000)

    assert win.panel.conn_panel.pause_btn.isChecked()
    assert win.plot.anchor_time == pytest.approx(2.0, abs=0.01)
    t = win.plot.analysis_packet["time"]
    t_trig = win.plot.anchor_time
    assert t[0] == pytest.approx(t_trig - 0.5, abs=0.006)
    assert t[-1] == pytest.approx(t_trig + 0.4, abs=0.006)
    assert "step +0.3 → +0" in panel.metrics_lbl.text()
    assert panel.metric_text(1, 0).endswith(" %") and panel.metric_text(1, 1) == ""
    assert panel.state_lbl.text() == "Idle" and not panel.arm_btn.isChecked()
    assert win.trigger_btn.text() == "T —" and win.plot.trigger_line() is None
    assert win.plot.capture_window() == pytest.approx((t_trig - 0.5, t_trig))  # before T
    assert win.panes.view == "step" and win.step_tab.lit  # the Step pane came forward
    assert win.lbl_status.text().startswith(f"Triggered at {t_trig:.3f} s")
    _disconnect(qtbot, win)


def test_second_capture_overlays_the_first_and_compares_metrics(qtbot: Any, tmp_path: Path) -> None:
    win = _window(qtbot, tmp_path)
    t = np.arange(400) * 0.005
    first = {"left_setpoint": np.where(t >= 1.0, 1.0, 0.0)}
    first["left_measurement"] = np.where(t >= 1.0, 1 - np.exp(-(t - 1.0).clip(0) / 0.1), 0.0)
    second = {k: v.copy() for k, v in first.items()}
    second["left_measurement"] = np.where(t >= 1.0, 1 - np.exp(-(t - 1.0).clip(0) / 0.2), 0.0)
    panel = win.panel.trigger_panel
    panel.setpoint_combo.setCurrentIndex(panel.setpoint_combo.findData("left_setpoint"))

    win._on_trigger_captured({"time": t, "signals": first, "signal_bounds": {}}, 1.0, "")
    assert win.plot.reference_count() == 0
    assert panel.metric_text(0, 1) == ""  # no previous capture yet
    win._on_trigger_captured({"time": t + 5, "signals": second, "signal_bounds": {}}, 6.0, "")

    assert win.plot.reference_count() == 2  # both visible signals, shifted onto this one
    # Rise time: 0.2 vs 0.1 s time constants, so twice as slow (worse).
    assert (panel.metric_text(0, 0), panel.metric_text(0, 1)) == ("0.440 s", "0.220 s")
    assert panel.metric_text(0, 2) == "▲ +100 %"
    win.panel.conn_panel.pause_btn.click()  # resume: the overlay goes away
    assert win.plot.reference_count() == 0
