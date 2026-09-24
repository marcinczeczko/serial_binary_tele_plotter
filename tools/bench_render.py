"""
Render budget check (roadmap R3.4): does the GUI hold its frame rate at full load?

The fixture: the bundled `pid` stream (34 signals), all visible, at 1 kHz on the simulated
port, with each buffer pre-filled to 100k samples. It runs the real pipeline: a
`TelemetryEngine` on its own QThread, reading `SimTransport` bytes on its `ReaderThread`,
feeding `SampleStore`s, with `LiveFeed` pulling snapshots into `TelemetryPlot` lanes.

Reported:
- frames drawn per second, and the per-frame cost (pull + draw, and paint)
- what the reader received, and anything lost on the way (CRC or sync errors, discarded
  bytes, counter gaps, or frames the simulator had to skip because the reader fell behind)

Pass: >= 30 FPS and nothing lost. The numbers come from the offscreen platform (software
raster, no GPU), so a desktop is usually faster; record them in docs/project-log.md.

Usage (needs Qt; from the repository root):
    uv run python tools/bench_render.py [--seconds 10] [--samples 100000] [--rate-hz 1000]
"""

from __future__ import annotations

import argparse
import copy
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets  # noqa: E402

from core.acquisition.engine import TelemetryEngine  # noqa: E402
from core.acquisition.storage import StreamStores  # noqa: E402
from core.config import DEFAULT_CONFIG_PATH, StreamConfigLoader  # noqa: E402
from core.simulation.synth import FrameSynth  # noqa: E402
from core.transport import SimTransport  # noqa: E402
from core.types import StreamConfig  # noqa: E402
from ui.charts.live_feed import LiveFeed  # noqa: E402
from ui.charts.telemetry_plot import TelemetryPlot  # noqa: E402

TARGET_FPS = 30.0
WARMUP_S = 1.0


def fixture_stream(rate_hz: float, visible: int) -> StreamConfig:
    """The bundled `pid` stream at `rate_hz`, with the first `visible` signals shown (0: all)."""
    cfg = copy.deepcopy(StreamConfigLoader(DEFAULT_CONFIG_PATH).get_stream("pid"))
    cfg["time"] = {"field": "loop_cntr", "scale_s": 1.0 / rate_hz, "step": 1}
    for i, sig in enumerate(cfg["signals"].values()):
        sig["visible"] = visible == 0 or i < visible
    return cfg


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--samples", type=int, default=100_000)
    parser.add_argument("--rate-hz", type=float, default=1000.0)
    parser.add_argument("--visible", type=int, default=0, help="signals shown (0: all)")
    args = parser.parse_args()

    app = QtWidgets.QApplication(sys.argv[:1])
    cfg = fixture_stream(args.rate_hz, args.visible)
    n_signals = sum(1 for sig in cfg["signals"].values() if sig.get("visible", True))

    stores = StreamStores(args.samples)
    engine = TelemetryEngine(args.samples, stores=stores)
    engine.sim_factory = lambda stream: SimTransport(stream, seed=1, start_frame=args.samples)
    thread = QtCore.QThread()
    engine.moveToThread(thread)
    thread.start()
    blocking = QtCore.Qt.ConnectionType.BlockingQueuedConnection
    QtCore.QMetaObject.invokeMethod(
        engine, "configure_streams", blocking, QtCore.Q_ARG(dict, {"pid": cfg})
    )
    QtCore.QMetaObject.invokeMethod(engine, "select_stream", blocking, QtCore.Q_ARG(str, "pid"))
    QtCore.QMetaObject.invokeMethod(
        engine,
        "start_working",
        blocking,
        QtCore.Q_ARG(str, "VIRTUAL"),
        QtCore.Q_ARG(int, 0),
    )
    # Pre-fill the window with history (the simulator continues after it).
    engine._on_bytes(FrameSynth(cfg, seed=0).frames(0, args.samples))

    plot = TelemetryPlot()
    plot.resize(1400, 900)
    plot.show()
    plot.configure_stream(cfg)
    feed = LiveFeed(stores.get("pid"), plot)

    tick_ms: list[float] = []
    frame_ts: list[float] = []
    paint_ms: list[float] = []
    original_tick = feed.tick

    def timed_tick() -> None:
        started = time.perf_counter()
        before = plot.last_packet
        original_tick()
        if plot.last_packet is not before:
            tick_ms.append((time.perf_counter() - started) * 1000)
            frame_ts.append(started)

    feed.timer.timeout.disconnect()
    feed.timer.timeout.connect(timed_tick)

    view: Any = plot.graphics
    original_paint = view.paintEvent

    def timed_paint(event: Any) -> None:
        started = time.perf_counter()
        original_paint(event)
        paint_ms.append((time.perf_counter() - started) * 1000)

    view.paintEvent = timed_paint

    stats_before = engine.parser.stats.snapshot()
    started = time.perf_counter()
    QtCore.QTimer.singleShot(int(args.seconds * 1000), app.quit)
    app.exec()
    elapsed = time.perf_counter() - started

    QtCore.QMetaObject.invokeMethod(engine, "stop_working", blocking)
    thread.quit()
    thread.wait(2000)
    stats = engine.parser.stats
    frames_rx = stats.frames_decoded - stats_before.frames_decoded
    lost = stats.errors + stats.discarded_bytes + stats.counter_gaps - stats_before.counter_gaps
    # Steady state: skip the first second (window setup, first layout and paint).
    steady = [t for t in frame_ts if t - started >= WARMUP_S]
    fps = (len(steady) - 1) / (steady[-1] - steady[0]) if len(steady) > 1 else 0.0

    def summary(values: list[float]) -> str:
        if not values:
            return "n/a"
        p95 = sorted(values)[int(0.95 * (len(values) - 1))]
        return f"mean {statistics.fmean(values):6.1f} ms  p95 {p95:6.1f} ms"

    print(
        f"fixture: {n_signals} signals visible x {args.samples:,} samples, "
        f"{args.rate_hz:.0f} Hz, {elapsed:.1f} s, lanes {plot.shown_lanes()}"
    )
    print(
        f"frames drawn: {len(tick_ms)}  ->  {fps:5.1f} FPS steady state (target {TARGET_FPS:.0f})"
    )
    print(f"pull + draw per frame: {summary(tick_ms)}")
    print(f"paint per frame:       {summary(paint_ms)}  ({len(paint_ms)} paints)")
    print(
        f"received: {frames_rx / elapsed:,.0f} frames/s; lost: {lost} "
        f"(CRC/size {stats.errors}, discarded {stats.discarded_bytes} B, "
        f"gaps {stats.counter_gaps - stats_before.counter_gaps})"
    )
    ok = fps >= TARGET_FPS and lost == 0
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
