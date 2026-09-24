"""
Live feed: pulls snapshots from the SampleStore at the GUI's own frame rate (R2.6).

This replaces the engine's 10 Hz push of full-buffer copies (P2/P3). The GUI asks for the
visible signals only, and only when the store's version has changed. At most one snapshot
exists at a time, so a slow frame can never create a backlog.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PyQt6 import QtCore

from core.acquisition.storage import SampleStore, Snapshot
from core.types import PlotMode, PlotPacketWithBounds
from ui.charts.telemetry_plot import TelemetryPlot

LIVE_FPS = 30


def to_packet(snapshot: Snapshot) -> PlotPacketWithBounds:
    return {"time": snapshot.time, "signals": snapshot.signals, "signal_bounds": snapshot.bounds}


class LiveFeed(QtCore.QObject):
    def __init__(
        self,
        store: SampleStore | None,
        plot: TelemetryPlot,
        sample_period_s: Callable[[], float],
        fps: int = LIVE_FPS,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._plot = plot
        self._sample_period_s = sample_period_s
        self._version: int | None = None
        self._base_interval_ms = max(1, round(1000 / fps))
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(self._base_interval_ms)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def set_store(self, store: SampleStore | None) -> None:
        """Shows another stream's store (or nothing); the next tick redraws."""
        self._store = store
        self.invalidate()

    def invalidate(self) -> None:
        """Forces the next tick to redraw (visible signals, stream or time base changed)."""
        self._version = None

    def tick(self) -> None:
        if self._plot.mode == PlotMode.ANALYSIS or self._store is None:
            return
        snapshot = self._store.snapshot(
            self._plot.visible_signal_ids(), self._version, self._sample_period_s()
        )
        if snapshot is None:
            return
        self._version = snapshot.version
        started = time.perf_counter()
        self._plot.show_packet(to_packet(snapshot))
        # Back off when a frame is expensive (many signals and samples), so the GUI thread
        # always has time left for input: spend at most about half of the time drawing.
        cost_ms = (time.perf_counter() - started) * 1000.0
        self.timer.setInterval(max(self._base_interval_ms, round(2 * cost_ms)))

    def freeze(self) -> PlotPacketWithBounds | None:
        """A snapshot of *all* signals, for analysis mode (so hidden ones can be shown)."""
        if self._store is None:
            return None
        snapshot = self._store.snapshot(None, None, self._sample_period_s())
        return to_packet(snapshot) if snapshot is not None else None
