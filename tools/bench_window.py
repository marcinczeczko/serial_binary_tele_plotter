"""
Whole-window render baseline (roadmap R10.0): where does the GUI thread's time go?

`bench_render.py` times the plot alone. This runs the real `MainWindow` (top bar, Signals
pane, Tune pane, QSS, B612) on the VIRTUAL port with the same fixture: the bundled `pid`
stream (34 signals), all visible, at 1 kHz, 100k samples of history. It answers R10.0's
question: is the plot the dominant cost of a frame, or the rest of the window?

Reported, per second of steady state on the GUI thread:
- CPU time of the GUI thread (`time.thread_time`), as a share of wall time
- of that: the live pull + draw (`LiveFeed.tick`) and the plot's paint; the remainder is
  everything else (other widgets' paints, layout, the top bar, readouts, event handling)
- `--cursor` sweeps the cursor over the plot at 60 Hz, which updates the Signals pane's
  readout (the normal state while someone reads values)
- `--paints` counts and times paint events per widget class (it routes every event through
  Python, so it inflates the totals; use it to rank, not to measure)
- `--profile` prints the top Python functions by own time (on Python 3.12+ cProfile also
  sees the other threads' Python, e.g. the simulator: read it as a ranking)

Run it on a real display (not the offscreen platform) to see what a user sees: on macOS or
Windows, `QT_QPA_PLATFORM` is left to Qt unless set. Nothing is written outside a temporary
folder (settings, the fixture's profile).

Usage (from the repository root):
    uv run python tools/bench_window.py [--seconds 8] [--fps 30] [--size 1440x900] [--cursor]
"""

from __future__ import annotations

import argparse
import copy
import cProfile
import json
import pstats
import statistics
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6 import QtCore, QtWidgets  # noqa: E402

from core.config import DEFAULT_CONFIG_PATH  # noqa: E402
from core.simulation.synth import FrameSynth  # noqa: E402
from core.transport import SimTransport  # noqa: E402
from core.types import EngineState  # noqa: E402
from styles import apply_dark_theme  # noqa: E402

WARMUP_S = 1.5
SAMPLES = 100_000  # the Samples box's maximum
CURSOR_HZ = 60


class TimedApp(QtWidgets.QApplication):
    """Times paint events per widget class (`--paints`)."""

    def __init__(self, argv: list[str]) -> None:
        super().__init__(argv)
        self.timing = False
        self.paints: dict[str, list[float]] = defaultdict(list)

    def notify(self, receiver: QtCore.QObject | None, event: QtCore.QEvent | None) -> bool:
        if not self.timing or event is None or event.type() != QtCore.QEvent.Type.Paint:
            return super().notify(receiver, event)
        started = time.perf_counter()
        handled = super().notify(receiver, event)
        name = type(receiver).__name__ if receiver is not None else "?"
        self.paints[name].append((time.perf_counter() - started) * 1000)
        return handled


def fixture_profile(folder: Path, rate_hz: float) -> Path:
    """The bundled profile with `pid` at `rate_hz` and every signal shown, in `folder`."""
    doc = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    pid = copy.deepcopy(doc["streams"]["pid"])
    pid["time"] = {**pid.get("time", {}), "scale_s": 1.0 / rate_hz}
    for sig in pid["signals"].values():
        sig["visible"] = True
    doc["streams"]["pid"] = pid
    path = folder / "bench_profile.json"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--rate-hz", type=float, default=1000.0)
    parser.add_argument("--fps", type=int, default=30, help="the live frame cap")
    parser.add_argument("--size", default="1440x900", help="window size, WxH")
    parser.add_argument("--cursor", action="store_true", help="sweep the cursor at 60 Hz")
    parser.add_argument("--paints", action="store_true", help="time paints per widget class")
    parser.add_argument("--profile", action="store_true", help="top Python functions")
    args = parser.parse_args()
    width, height = (int(v) for v in args.size.lower().split("x"))

    # Overriding notify() routes every event through Python: only when asked.
    app = TimedApp(sys.argv[:1]) if args.paints else QtWidgets.QApplication(sys.argv[:1])
    QtCore.QLocale.setDefault(QtCore.QLocale.c())
    apply_dark_theme(app)

    from ui.main_window import MainWindow

    tmp = tempfile.TemporaryDirectory(prefix="bench_window_")
    folder = Path(tmp.name)
    settings = QtCore.QSettings(str(folder / "s.ini"), QtCore.QSettings.Format.IniFormat)
    win = MainWindow(fixture_profile(folder, args.rate_hz), settings)
    win.engine.sim_factory = lambda stream: SimTransport(stream, seed=1, start_frame=SAMPLES)
    win.resize(width, height)
    win.show()
    win.panel.select_stream("pid")
    win.panel.time_panel.samples_sb.setValue(SAMPLES)

    feed = win.live_feed
    feed._base_interval_ms = max(1, round(1000 / args.fps))
    feed.timer.setInterval(feed._base_interval_ms)

    conn = win.panel.conn_panel
    conn.port_combo.setCurrentIndex(conn.port_combo.findText("VIRTUAL"))
    conn.connect_btn.click()
    deadline = time.monotonic() + 10
    store = win.stores.get("pid")
    while (
        win.engine_state != EngineState.RUNNING or store is None or store.capacity != SAMPLES
    ) and time.monotonic() < deadline:
        app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50)
        store = win.stores.get("pid")
    cfg = win.panel.get_current_stream_config()
    if win.engine_state != EngineState.RUNNING or store is None or cfg is None:
        print("could not start the VIRTUAL port")
        return 1
    # Pre-fill the window with history (the simulator continues after it), as bench_render.
    win.engine._on_bytes(FrameSynth(cfg, seed=0).frames(0, SAMPLES))

    tick_ms: list[float] = []
    frame_ts: list[float] = []
    paint_ms: list[float] = []
    original_tick = feed.tick

    def timed_tick() -> None:
        started = time.perf_counter()
        before = win.plot.last_packet
        original_tick()
        if win.plot.last_packet is not before:
            tick_ms.append((time.perf_counter() - started) * 1000)
            frame_ts.append(started)

    feed.timer.timeout.disconnect()
    feed.timer.timeout.connect(timed_tick)

    view: Any = win.plot.graphics
    original_paint = view.paintEvent

    def timed_paint(event: Any) -> None:
        started = time.perf_counter()
        original_paint(event)
        paint_ms.append((time.perf_counter() - started) * 1000)

    view.paintEvent = timed_paint

    sweep = QtCore.QTimer()
    sweep.setInterval(round(1000 / CURSOR_HZ))
    phase = [0.0]

    def move_cursor() -> None:
        packet = win.plot.last_packet
        if packet is None or len(packet["time"]) < 2:
            return
        t = packet["time"]
        phase[0] = (phase[0] + 0.004) % 1.0
        win.plot.move_cursor(float(t[0] + (t[-1] - t[0]) * (0.1 + 0.8 * phase[0])))

    sweep.timeout.connect(move_cursor)
    if args.cursor:
        sweep.start()

    profiler = cProfile.Profile() if args.profile else None
    window: dict[str, float] = {}

    def begin() -> None:
        tick_ms.clear()
        paint_ms.clear()
        frame_ts.clear()
        if isinstance(app, TimedApp):
            app.paints.clear()
            app.timing = True
        window["wall"] = time.perf_counter()
        window["cpu"] = time.thread_time()
        if profiler is not None:
            profiler.enable()

    def end() -> None:
        if profiler is not None:
            profiler.disable()
        window["wall"] = time.perf_counter() - window["wall"]
        window["cpu"] = time.thread_time() - window["cpu"]
        if isinstance(app, TimedApp):
            app.timing = False
        app.quit()

    stats_before = win.engine.link.stats.snapshot()
    QtCore.QTimer.singleShot(int(WARMUP_S * 1000), begin)
    QtCore.QTimer.singleShot(int((WARMUP_S + args.seconds) * 1000), end)
    app.exec()
    sweep.stop()

    stats = win.engine.link.stats
    lost = stats.errors + stats.discarded_bytes + stats.counter_gaps - stats_before.counter_gaps
    shown = win.plot.visible_signal_ids()
    wall = window["wall"]
    fps = (len(frame_ts) - 1) / (frame_ts[-1] - frame_ts[0]) if len(frame_ts) > 1 else 0.0
    per_s = 1000.0 / wall  # ms of work per second of wall time -> share of the GUI thread
    cpu = window["cpu"] * per_s
    feed_total = sum(tick_ms) / wall
    paint_total = sum(paint_ms) / wall
    other = cpu - feed_total - paint_total

    def summary(values: list[float]) -> str:
        if not values:
            return "n/a"
        p95 = sorted(values)[int(0.95 * (len(values) - 1))]
        return f"mean {statistics.fmean(values):6.1f} ms  p95 {p95:6.1f} ms"

    platform = QtWidgets.QApplication.platformName()
    ratio = win.devicePixelRatioF()
    print(
        f"window {width}x{height} @ {ratio:g}x on '{platform}', {len(shown)} signals x "
        f"{SAMPLES:,} samples, {args.rate_hz:.0f} Hz, cursor sweep {'on' if args.cursor else 'off'}"
    )
    print(f"lanes {win.plot.shown_lanes()}, {wall:.1f} s steady state")
    print(f"frames drawn: {len(tick_ms)}  ->  {fps:5.1f} FPS (cap {args.fps})")
    print(f"pull + draw per frame: {summary(tick_ms)}")
    print(f"plot paint per frame:  {summary(paint_ms)}  ({len(paint_ms)} paints)")
    print(f"GUI thread CPU: {cpu:6.1f} ms/s ({cpu / 10:4.1f}% busy)")
    for name, value in (
        ("pull + draw", feed_total),
        ("plot paint", paint_total),
        ("rest of window", other),
    ):
        share = value / cpu * 100 if cpu > 0 else 0.0
        print(f"  {name:<15} {value:6.1f} ms/s  {share:5.1f}%")
    print(f"lost: {lost} (CRC/size {stats.errors}, discarded {stats.discarded_bytes} B)")
    if isinstance(app, TimedApp):
        print("paints by widget class (inflated by timing every event):")
        ranked = sorted(app.paints.items(), key=lambda kv: -sum(kv[1]))
        for name, values in ranked[:15]:
            print(f"  {name:<28} {len(values):6d} x  {sum(values) / wall:6.1f} ms/s")
    if profiler is not None:
        print("GUI thread, top Python functions by own time:")
        pstats.Stats(profiler).sort_stats("tottime").print_stats(20)

    win.close()
    tmp.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
