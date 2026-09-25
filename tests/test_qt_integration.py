"""
Integration tests against real Qt (pytest-qt, offscreen platform).

Marked `qt`: they are skipped automatically when pytest-qt is disabled (`-p no:pytest-qt`) or
Qt cannot be loaded (e.g. headless machines without libEGL). See tests/conftest.py.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6 import QtCore  # noqa: E402

from core.acquisition.engine import TelemetryEngine  # noqa: E402
from core.protocol.stats import LinkReport  # noqa: E402
from core.types import EngineState, StreamConfig  # noqa: E402

pytestmark = pytest.mark.qt

REPO_ROOT = Path(__file__).resolve().parent.parent

IMU_STREAM: StreamConfig = {
    "name": "IMU test",
    "frame": {
        "stream_id": 3,
        "endianness": "little",
        "fields": [
            {"name": "loop_cntr", "type": "u32"},
            {"name": "acc_x", "type": "f32"},
            {"name": "gyro_z", "type": "f32"},
        ],
    },
    "signals": {
        "ax": {"label": "Acc X", "field": "acc_x", "color": "#fff", "visible": True},
        "gz": {"label": "Gyro Z", "field": "gyro_z", "color": "#fff", "visible": True},
    },
}


class _Receiver(QtCore.QObject):
    """GUI-thread receiver: records which thread each queued delivery ran on."""

    def __init__(self) -> None:
        super().__init__()
        self.reports: list[LinkReport] = []
        self.delivery_threads: list[Any] = []

    @QtCore.pyqtSlot(dict)
    def on_stats(self, report: LinkReport) -> None:
        self.delivery_threads.append(QtCore.QThread.currentThread())
        self.reports.append(report)


def test_engine_in_worker_thread_fills_shared_store(qtbot: Any) -> None:
    engine = TelemetryEngine(max_samples=500)
    engine.stats_timer.setInterval(100)
    thread = QtCore.QThread()
    engine.moveToThread(thread)
    receiver = _Receiver()
    engine.link_stats.connect(receiver.on_stats)  # auto -> queued (different threads)
    stores = engine.stores  # shared with the GUI; safe to read from this thread
    thread.start()
    queued = QtCore.Qt.ConnectionType.QueuedConnection
    try:
        QtCore.QMetaObject.invokeMethod(
            engine, "configure_streams", queued, QtCore.Q_ARG(dict, {"imu": IMU_STREAM})
        )
        QtCore.QMetaObject.invokeMethod(engine, "select_stream", queued, QtCore.Q_ARG(str, "imu"))
        QtCore.QMetaObject.invokeMethod(
            engine,
            "start_working",
            queued,
            QtCore.Q_ARG(str, "VIRTUAL"),
            QtCore.Q_ARG(int, 115200),
        )

        qtbot.waitUntil(lambda: len(stores.get("imu") or []) >= 10, timeout=5000)
        store = stores.get("imu")
        assert store is not None
        qtbot.waitUntil(lambda: any(r["samples_per_s"] > 0 for r in receiver.reports), timeout=5000)

        assert engine.thread() is thread
        app = QtCore.QCoreApplication.instance()
        assert app is not None
        gui_thread = app.thread()
        assert all(t is gui_thread for t in receiver.delivery_threads)

        snap = store.snapshot(["ax"])
        assert snap is not None
        assert list(snap.signals) == ["ax"]
        assert len(snap.time) == len(snap.signals["ax"]) >= 10
        assert snap.time[-1] > snap.time[0]
    finally:
        QtCore.QMetaObject.invokeMethod(
            engine, "stop_working", QtCore.Qt.ConnectionType.BlockingQueuedConnection
        )
        thread.quit()
        assert thread.wait(2000)
    assert engine.state == EngineState.CONFIGURED


def _load_stream(key: str) -> StreamConfig:
    with (REPO_ROOT / "streams.json").open(encoding="utf-8") as f:
        streams: dict[str, StreamConfig] = json.load(f)["streams"]
    return streams[key]


def test_stream_editor_round_trip_is_lossless_for_pid_stream(qtbot: Any) -> None:
    from ui.config.stream_editor import StreamEditor

    original = _load_stream("pid")
    editor = StreamEditor()
    qtbot.addWidget(editor)
    editor.load_data("pid", copy.deepcopy(original))

    key, data = editor.get_data()

    assert key == "pid"
    assert data == original


def test_stream_editor_round_trip_keeps_line_width(qtbot: Any) -> None:
    from ui.config.stream_editor import StreamEditor

    original = _load_stream("imu_6axis")
    editor = StreamEditor()
    qtbot.addWidget(editor)
    editor.load_data("imu_6axis", copy.deepcopy(original))

    _, data = editor.get_data()

    widths = {k: v["line"]["width"] for k, v in data["signals"].items()}
    assert widths == {k: v["line"]["width"] for k, v in original["signals"].items()}


def test_stream_editor_round_trip_keeps_unknown_signal_keys(qtbot: Any) -> None:
    from ui.config.stream_editor import StreamEditor

    original = _load_stream("imu_6axis")
    editor = StreamEditor()
    qtbot.addWidget(editor)
    editor.load_data("imu_6axis", copy.deepcopy(original))

    _, data = editor.get_data()

    assert data["signals"] == original["signals"]


def test_main_window_starts_switches_stream_and_closes(qtbot: Any, monkeypatch: Any) -> None:
    from ui.main_window import MainWindow

    monkeypatch.chdir(REPO_ROOT.parent)  # C10: must not depend on the working directory
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    panel = win.panel
    assert panel.stream_tabs.count() >= 2
    win.tune_tab.click()  # its lit tab closes the right pane, and opens it again
    assert win.right_stack.isHidden() and not win.tune_tab.lit
    win.tune_tab.click()
    assert not win.right_stack.isHidden() and win.tune_tab.lit
    panel.stream_tabs.setCurrentIndex(1)
    qtbot.wait(50)
    current = panel.get_current_stream_config()
    assert current is not None
    assert set(win.plot.signal_views) == set(current["signals"])

    win.close()
    assert win.engine_thread.isFinished()


def test_plot_downsampling_is_enabled_and_pens_default_to_1px(qtbot: Any) -> None:
    from ui.charts.telemetry_plot import TelemetryPlot

    plot = TelemetryPlot()
    qtbot.addWidget(plot)
    plot.configure_signals(
        {
            "a": {"label": "A", "field": "a", "color": "#fff"},
            "b": {"label": "B", "field": "b", "color": "#fff", "line": {"width": 3}},
        }
    )

    curve_a = plot.signal_views["a"]["curve"]
    # Live frames come decimated from the store's level of detail (R3.4): pyqtgraph's own
    # downsampling would only add cost. Paused, the full-resolution capture needs it (P1).
    assert curve_a.opts["autoDownsample"] is False
    plot.set_paused(True)
    _, auto, method = plot.plot.downsampleMode()
    assert (auto, method) == (True, "peak")
    assert curve_a.opts["autoDownsample"] is True
    assert curve_a.opts["clipToView"] is True
    plot.set_paused(False)
    assert curve_a.opts["autoDownsample"] is False
    assert curve_a.opts["pen"].width() == 1
    assert plot.signal_views["b"]["curve"].opts["pen"].width() == 3


def test_main_window_switches_stream_while_running(qtbot: Any, monkeypatch: Any) -> None:
    from ui.main_window import MainWindow

    monkeypatch.chdir(REPO_ROOT.parent)
    win = MainWindow()
    qtbot.addWidget(win)
    conn = win.panel.conn_panel
    conn.port_combo.setCurrentIndex(conn.port_combo.findText("VIRTUAL"))

    conn.connect_btn.click()
    qtbot.waitUntil(lambda: win.engine_state == EngineState.RUNNING, timeout=5000)
    # The status arrives in its own queued event, right after the state change.
    qtbot.waitUntil(lambda: win.lbl_status.text() == "Connected to VIRTUAL", timeout=5000)

    imu_index = win.panel.stream_tabs.findData("imu_6axis")
    win.panel.stream_tabs.setCurrentIndex(imu_index)
    imu_signals = {
        k for k, v in _load_stream("imu_6axis")["signals"].items() if v.get("visible", True)
    }

    def receiving_imu() -> bool:
        packet = win.plot.last_packet
        return packet is not None and set(packet["signals"]) == imu_signals

    qtbot.waitUntil(receiving_imu, timeout=5000)
    assert win.engine_state == EngineState.RUNNING

    win.close()
    assert win.engine_thread.isFinished()
    assert win.engine.state == EngineState.CONFIGURED  # safe: its thread has finished


# --- Config editor data safety (C4 / R1.6) ---


@pytest.fixture
def message_boxes(monkeypatch: Any) -> list[tuple[str, str]]:
    """Records QMessageBox calls as (kind, text) instead of opening modal dialogs."""
    from PyQt6 import QtWidgets

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
def config_copy(tmp_path: Path, monkeypatch: Any, message_boxes: Any) -> Path:
    """A private copy of streams.json in a temp CWD, so tests never touch the repo's file."""
    path = tmp_path / "streams.json"
    path.write_bytes((REPO_ROOT / "streams.json").read_bytes())
    monkeypatch.chdir(tmp_path)
    return path


def _select(tab: Any, key: str) -> None:
    tab.select_stream(key)
    assert tab.current_key() == key


def _tab(qtbot: Any, path: Path) -> Any:
    from core.config import StreamConfigLoader
    from ui.config.tab import ConfiguratorTab

    tab = ConfiguratorTab(StreamConfigLoader(path))
    qtbot.addWidget(tab)
    return tab


def test_config_save_without_edits_is_byte_identical(qtbot: Any, config_copy: Path) -> None:
    original = config_copy.read_bytes()
    tab = _tab(qtbot, config_copy)
    for index in range(tab.stream_tabs.count()):  # visit every stream
        tab.stream_tabs.setCurrentIndex(index)
    tab.stream_tabs.setCurrentIndex(0)
    assert not tab.is_dirty()

    tab.save_to_file()

    assert config_copy.read_bytes() == original
    assert (config_copy.parent / "streams.json.bak").read_bytes() == original


def test_config_edits_survive_switching_streams(qtbot: Any, config_copy: Path) -> None:
    tab = _tab(qtbot, config_copy)
    tab.stream_tabs.setCurrentIndex(0)
    tab.editor.name_edit.setText("Renamed PID")
    tab.editor.name_edit.textEdited.emit("Renamed PID")  # still being typed: applied on the switch
    tab.stream_tabs.setCurrentIndex(1)
    tab.stream_tabs.setCurrentIndex(0)
    assert tab.editor.name_edit.text() == "Renamed PID"
    assert tab.stream_tabs.tabText(0) == "Renamed PID · 0x01"
    assert tab.is_dirty()

    tab.save_to_file()

    saved = json.loads(config_copy.read_text(encoding="utf-8"))
    assert saved["streams"]["pid"]["name"] == "Renamed PID"
    assert not tab.is_dirty()


def test_config_plotting_a_field_adds_a_signal_named_after_it(
    qtbot: Any, config_copy: Path
) -> None:
    tab = _tab(qtbot, config_copy)
    _select(tab, "imu_6axis")
    editor = tab.editor
    editor.select("gyro_y")
    assert editor.selected_signal is None and editor.plot_btn.text() == "Plot this field"

    editor.plot_btn.click()

    _, data = editor.get_data()
    assert data["signals"]["gyro_y"]["field"] == "gyro_y"
    assert data["signals"]["gyro_y"]["label"] == "gyro_y"
    assert editor.selected_signal == "gyro_y" and editor.label_edit.text() == "gyro_y"


def test_config_renaming_a_field_follows_its_signal(qtbot: Any, config_copy: Path) -> None:
    tab = _tab(qtbot, config_copy)
    _select(tab, "imu_6axis")
    editor = tab.editor
    editor.select("acc_x")
    editor.field_name_edit.setText("accel_x")
    editor.field_name_edit.editingFinished.emit()

    _, data = editor.get_data()
    assert data["signals"]["acc_x"]["field"] == "accel_x"
    names = [editor.time_field_combo.itemText(i) for i in range(editor.time_field_combo.count())]
    assert "accel_x" in names and "acc_x" not in names

    editor.field_name_edit.setText("acc_y")  # taken: refused, and said why
    editor.field_name_edit.editingFinished.emit()
    assert editor.field_name_edit.text() == "accel_x"
    assert "already a field 'acc_y'" in tab.status_lbl.text()


def test_config_save_refuses_invalid_document(
    qtbot: Any, config_copy: Path, message_boxes: list[tuple[str, str]]
) -> None:
    original = config_copy.read_bytes()
    tab = _tab(qtbot, config_copy)
    _select(tab, "imu_6axis")
    tab.editor.select("loop_cntr")
    tab.editor.remove_field_btn.click()
    assert "loop_cntr" in tab.status_lbl.text()  # the problem shows as you edit

    tab.save_to_file()

    assert config_copy.read_bytes() == original
    ((kind, text),) = message_boxes
    assert kind == "critical"
    assert "'loop_cntr'" in text


def test_main_window_keeps_selected_stream_after_config_save(qtbot: Any, config_copy: Path) -> None:
    from ui.main_window import MainWindow

    win = MainWindow(config_copy)
    qtbot.addWidget(win)
    imu_index = win.panel.stream_tabs.findData("imu_6axis")
    win.panel.stream_tabs.setCurrentIndex(imu_index)

    win.configurator.save_to_file()

    assert win.panel.stream_tabs.currentData() == "imu_6axis"
    assert set(win.plot.signal_views) == set(_load_stream("imu_6axis")["signals"])
    win.close()


def test_plot_handles_signals_without_data(qtbot: Any) -> None:
    import numpy as np

    from ui.charts.telemetry_plot import TelemetryPlot

    plot = TelemetryPlot()
    qtbot.addWidget(plot)
    plot.configure_signals(
        {
            "real": {"label": "Real", "field": "a", "color": "#fff"},
            "ghost": {"label": "Ghost", "field": "b", "color": "#f00"},
        }
    )
    t = np.arange(10, dtype=float)
    plot.show_packet(
        {
            "time": t,
            "signals": {"real": np.linspace(-2.0, 3.0, 10), "ghost": np.full(10, np.nan)},
            "signal_bounds": {"real": (-2.0, 3.0)},
        }
    )
    lo, hi = plot.plot.getViewBox().viewRange()[1]
    assert np.isfinite([lo, hi]).all()
    assert lo <= -2.0 and hi >= 3.0

    plot.move_cursor(4.5)
    assert "Ghost: n/a" in plot.readout_text()


def test_main_parse_args_keeps_qt_options() -> None:
    from main import parse_args

    args, rest = parse_args(["--config", "robot.json", "-platform", "offscreen"])
    assert args.config == "robot.json"
    assert rest == ["-platform", "offscreen"]
    args, rest = parse_args([])
    assert args.config is None and rest == []


def test_engine_thread_reads_transport_and_handles_disconnect(qtbot: Any) -> None:
    from tests.fakes import FakeTransport
    from tests.test_acquisition_engine import _CFG_BYTES, _frames

    blob = _frames(300)
    transport = FakeTransport(
        [blob[i : i + 64] for i in range(0, len(blob), 64)], fail_when_drained=True
    )
    engine = TelemetryEngine(max_samples=1000)
    engine.transport_factory = lambda port, baud: transport
    thread = QtCore.QThread()
    engine.moveToThread(thread)
    failures: list[str] = []
    engine.connection_failed.connect(failures.append)
    thread.start()
    queued = QtCore.Qt.ConnectionType.QueuedConnection
    try:
        QtCore.QMetaObject.invokeMethod(
            engine, "configure_streams", queued, QtCore.Q_ARG(dict, {"a": _CFG_BYTES})
        )
        QtCore.QMetaObject.invokeMethod(
            engine, "start_working", queued, QtCore.Q_ARG(str, "COM9"), QtCore.Q_ARG(int, 1)
        )
        qtbot.waitUntil(lambda: bool(failures), timeout=5000)
    finally:
        thread.quit()
        assert thread.wait(2000)

    assert failures == ["Serial error: device disconnected"]
    assert engine.state == EngineState.CONFIGURED
    assert transport.closed
    assert engine.link.stats.frames_decoded == 300
    a_store = engine.stores.get("a")
    assert a_store is not None and a_store.total_stored == 300


# --- Live feed: GUI pulls from the store (R2.6) ---


def _feed_setup(qtbot: Any) -> tuple[Any, Any, Any]:
    from core.acquisition.storage import SampleStore
    from core.acquisition.timebase import TimeBaseConfig
    from ui.charts.live_feed import LiveFeed
    from ui.charts.telemetry_plot import TelemetryPlot

    signals: dict[str, Any] = {
        "a": {"label": "A", "field": "a", "color": "#fff"},
        "b": {"label": "B", "field": "b", "color": "#f00", "visible": False},
    }
    store = SampleStore(1000)
    store.configure(signals, TimeBaseConfig(scale_s=0.01))
    plot = TelemetryPlot()
    qtbot.addWidget(plot)
    plot.configure_signals(signals)
    feed = LiveFeed(store, plot)
    feed.timer.stop()  # drive ticks by hand
    return store, plot, feed


def _frames_ab(start: int, n: int) -> list[dict[str, float]]:
    return [{"loop_cntr": i, "a": float(i), "b": -float(i)} for i in range(start, start + n)]


def test_live_feed_pulls_only_visible_signals_and_only_when_changed(qtbot: Any) -> None:
    store, plot, feed = _feed_setup(qtbot)
    drawn: list[Any] = []
    original = plot.show_packet

    def recording_show(packet: Any, prepared: Any = None) -> None:
        drawn.append(packet)
        original(packet, prepared)

    plot.show_packet = recording_show

    feed.tick()
    assert drawn == []  # empty store
    store.append(_frames_ab(0, 50))
    feed.tick()
    feed.tick()  # nothing new: no second draw
    assert len(drawn) == 1
    assert list(drawn[0]["signals"]) == ["a"]  # "b" is hidden: not copied

    plot.set_signal_visible("b", True)
    feed.invalidate()
    feed.tick()
    assert list(drawn[-1]["signals"]) == ["a", "b"]


def test_pause_freezes_all_signals_while_acquisition_continues(qtbot: Any) -> None:
    store, plot, feed = _feed_setup(qtbot)
    store.append(_frames_ab(0, 20))
    feed.tick()

    plot.set_paused(True, feed.freeze())
    store.append(_frames_ab(20, 30))
    feed.tick()  # ignored while paused

    frozen = plot.analysis_packet
    assert frozen is not None
    assert set(frozen["signals"]) == {"a", "b"}  # hidden "b" included for analysis
    assert frozen["time"][-1] == 19 * 0.01
    b_curve = plot.signal_views["b"]["curve"]
    assert len(b_curve.yData) == 20  # hidden curve has data if it is shown while paused

    plot.set_paused(False)
    feed.invalidate()
    feed.tick()
    assert plot.last_packet is not None
    assert plot.last_packet["time"][-1] == 49 * 0.01


def test_switching_streams_is_a_view_change_that_keeps_history(qtbot: Any) -> None:
    from ui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    panel = win.panel
    panel.stream_tabs.setCurrentIndex(panel.stream_tabs.findData("imu_6axis"))
    conn = panel.conn_panel
    conn.port_combo.setCurrentIndex(conn.port_combo.findText("VIRTUAL"))
    conn.connect_btn.click()
    qtbot.waitUntil(lambda: win.engine_state == EngineState.RUNNING, timeout=5000)

    def imu_count() -> int:
        store = win.stores.get("imu_6axis")
        return len(store) if store is not None else 0

    qtbot.waitUntil(lambda: imu_count() >= 20, timeout=5000)
    states: list[EngineState] = []
    win.engine.state_changed.connect(states.append)

    panel.stream_tabs.setCurrentIndex(panel.stream_tabs.findData("pid"))
    qtbot.wait(100)
    kept = imu_count()
    panel.stream_tabs.setCurrentIndex(panel.stream_tabs.findData("imu_6axis"))
    qtbot.waitUntil(lambda: win.plot.last_packet is not None, timeout=5000)

    assert kept >= 20  # imu history survived showing another stream
    assert states == []  # no stop/restart on a view change
    assert win.engine_state == EngineState.RUNNING
    win.close()


# --- Per-stream time base (R2.5) ---


def test_stream_editor_time_base_round_trip_and_edits(qtbot: Any) -> None:
    from ui.config.stream_editor import StreamEditor

    editor = StreamEditor()
    qtbot.addWidget(editor)
    bare = copy.deepcopy(_load_stream("imu_6axis"))
    del bare["time"]
    editor.load_data("imu", copy.deepcopy(bare))
    assert "time" not in editor.get_data()[1]  # defaults don't add a block
    fields = [editor.time_field_combo.itemText(i) for i in range(editor.time_field_combo.count())]
    assert fields == [f["name"] for f in bare["frame"]["fields"]]
    assert editor.time_scale_edit.text() == "5 ms"

    editor.time_scale_edit.setText("1e-06")
    editor.time_scale_edit.editingFinished.emit()
    assert editor.time_scale_edit.text() == "1 µs"
    editor.time_step_edit.setText("5000")
    editor.time_step_edit.textEdited.emit("5000")  # typed, not left yet: kept by the redraw
    editor.time_field_combo.setCurrentText("motor")
    editor.time_field_combo.activated.emit(editor.time_field_combo.currentIndex())
    assert editor.get_data()[1]["time"] == {"field": "motor", "scale_s": 1e-06, "step": 5000}

    editor.time_scale_edit.setText("2.5 ms")
    editor.time_scale_edit.editingFinished.emit()
    assert editor.get_data()[1]["time"]["scale_s"] == 0.0025

    with_extra = {**bare, "time": {"step": 1, "note": "kept", "scale_s": 0.005}}
    editor.load_data("imu", copy.deepcopy(with_extra))
    assert editor.get_data()[1]["time"] == with_extra["time"]  # same keys, same order


def test_period_is_per_stream_and_an_override_retimes_history(
    qtbot: Any, config_copy: Path
) -> None:
    from ui.main_window import MainWindow

    doc = json.loads(config_copy.read_text(encoding="utf-8"))
    doc["streams"]["imu_6axis"]["time"]["scale_s"] = 0.01
    config_copy.write_text(json.dumps(doc, indent=4), encoding="utf-8")
    win = MainWindow(config_copy)
    qtbot.addWidget(win)
    panel, time_panel = win.panel, win.panel.time_panel
    qtbot.waitUntil(lambda: win.stores.get("imu_6axis") is not None, timeout=5000)
    imu_store, pid_store = win.stores.get("imu_6axis"), win.stores.get("pid")
    assert imu_store is not None and pid_store is not None

    panel.stream_tabs.setCurrentIndex(panel.stream_tabs.findData("imu_6axis"))
    assert time_panel.get_period() == pytest.approx(10.0)  # from its time block
    assert not time_panel.is_overridden()

    time_panel.period_sb.setValue(20.0)
    qtbot.waitUntil(lambda: imu_store.time_scale_s == pytest.approx(0.02), timeout=5000)
    assert time_panel.is_overridden()

    panel.stream_tabs.setCurrentIndex(panel.stream_tabs.findData("pid"))
    assert time_panel.get_period() == pytest.approx(5.0)
    assert not time_panel.is_overridden()
    assert pid_store.time_scale_s == pytest.approx(0.005)  # other streams are untouched
    panel.stream_tabs.setCurrentIndex(panel.stream_tabs.findData("imu_6axis"))
    assert time_panel.get_period() == pytest.approx(20.0)  # the override is remembered

    win.configurator.save_to_file()  # reload: the file's values apply again
    assert time_panel.get_period() == pytest.approx(10.0)
    assert not time_panel.is_overridden()
    win.close()
