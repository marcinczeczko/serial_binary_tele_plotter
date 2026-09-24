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
from core.types import EngineState, PlotPacketWithRaw, StreamConfig  # noqa: E402

pytestmark = pytest.mark.qt

REPO_ROOT = Path(__file__).resolve().parent.parent

IMU_STREAM: StreamConfig = {
    "name": "IMU test",  # "imu" in the name selects the IMU waveform in VirtualDevice
    "panel_type": "none",
    "frame": {"stream_id": 3, "endianness": "little", "fields": []},
    "signals": {
        "ax": {"label": "Acc X", "field": "acc_x", "color": "#fff", "visible": True},
        "gz": {"label": "Gyro Z", "field": "gyro_z", "color": "#fff", "visible": True},
    },
}


class _Receiver(QtCore.QObject):
    """GUI-thread receiver: records which thread each queued delivery ran on."""

    def __init__(self) -> None:
        super().__init__()
        self.packets: list[PlotPacketWithRaw] = []
        self.reports: list[LinkReport] = []
        self.delivery_threads: list[Any] = []

    @QtCore.pyqtSlot(dict)
    def on_data(self, packet: PlotPacketWithRaw) -> None:
        self.delivery_threads.append(QtCore.QThread.currentThread())
        self.packets.append(packet)

    @QtCore.pyqtSlot(dict)
    def on_stats(self, report: LinkReport) -> None:
        self.reports.append(report)


def test_engine_in_worker_thread_delivers_packets_to_gui_thread(qtbot: Any) -> None:
    engine = TelemetryEngine(sample_period_ms=5.0, max_samples=500)
    engine.stats_timer.setInterval(100)
    thread = QtCore.QThread()
    engine.moveToThread(thread)
    receiver = _Receiver()
    engine.data_ready.connect(receiver.on_data)  # auto -> queued (different threads)
    engine.link_stats.connect(receiver.on_stats)
    thread.start()
    queued = QtCore.Qt.ConnectionType.QueuedConnection
    try:
        QtCore.QMetaObject.invokeMethod(
            engine, "configure_signals", queued, QtCore.Q_ARG(dict, IMU_STREAM["signals"])
        )
        QtCore.QMetaObject.invokeMethod(
            engine, "configure_frame", queued, QtCore.Q_ARG(dict, IMU_STREAM)
        )
        QtCore.QMetaObject.invokeMethod(
            engine,
            "start_working",
            queued,
            QtCore.Q_ARG(str, "VIRTUAL"),
            QtCore.Q_ARG(int, 115200),
        )

        qtbot.waitUntil(lambda: len(receiver.packets) >= 2, timeout=5000)
        qtbot.waitUntil(lambda: any(r["samples_per_s"] > 0 for r in receiver.reports), timeout=5000)

        assert engine.thread() is thread
        app = QtCore.QCoreApplication.instance()
        assert app is not None
        gui_thread = app.thread()
        assert all(t is gui_thread for t in receiver.delivery_threads)

        packet = receiver.packets[-1]
        assert set(packet["signals"]) == {"ax", "gz"}
        assert len(packet["time"]) >= 2
        assert len(packet["time"]) == len(packet["signals"]["ax"])
        assert packet["time"][-1] > packet["time"][0]
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


@pytest.mark.xfail(strict=True, reason="C4: editor drops keys it doesn't own, e.g. 'group' (R1.6)")
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

    monkeypatch.chdir(REPO_ROOT)  # streams.json is resolved relative to the CWD (C10)
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    panel = win.panel
    assert panel.payload_combo.count() >= 2
    panel.pid_section.header.click()  # expand, then collapse the PID section
    panel.pid_section.header.click()
    panel.payload_combo.setCurrentIndex(1)
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

    _, auto, method = plot.plot.downsampleMode()
    assert (auto, method) == (True, "peak")
    curve_a = plot.signal_views["a"]["curve"]
    assert curve_a.opts["autoDownsample"] is True
    assert curve_a.opts["clipToView"] is True
    assert curve_a.opts["pen"].width() == 1
    assert plot.signal_views["b"]["curve"].opts["pen"].width() == 3
