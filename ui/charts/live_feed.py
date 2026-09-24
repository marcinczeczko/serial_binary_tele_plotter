"""
Live feed: pulls the store's overview at the GUI's own frame rate (R2.6, R3.4).

The GUI asks for the visible signals only, and only when the store's version changed, so
a slow frame can never cause a backlog. A frame is the store's incremental min/max level
of detail (`SampleStore.overview`): about 1500 buckets per signal, whatever the buffer
size. The live cursor readout asks the store for exact values instead
(`SampleStore.values_at`).
"""

from __future__ import annotations

import time

from PyQt6 import QtCore

from core.acquisition.storage import SampleStore, Snapshot
from core.types import PlotMode, PlotPacketWithBounds
from ui.charts.series import DrawData
from ui.charts.telemetry_plot import TelemetryPlot

LIVE_FPS = 30


def to_packet(snapshot: Snapshot) -> PlotPacketWithBounds:
    return {"time": snapshot.time, "signals": snapshot.signals, "signal_bounds": snapshot.bounds}


class LiveFeed(QtCore.QObject):
    def __init__(
        self,
        store: SampleStore | None,
        plot: TelemetryPlot,
        fps: int = LIVE_FPS,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._store: SampleStore | None = None
        self._plot = plot
        self._version: int | None = None
        self._base_interval_ms = max(1, round(1000 / fps))
        self.timer = QtCore.QTimer(self)
        # Coarse timers may fire up to 5% late, which alone caps 30 FPS at ~28.
        self.timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self.timer.setInterval(self._base_interval_ms)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.set_store(store)

    def set_store(self, store: SampleStore | None) -> None:
        """Shows another stream's store (or nothing); the next tick redraws."""
        self._store = store
        self._plot.value_source = store.values_at if store is not None else None
        self.invalidate()

    def invalidate(self) -> None:
        """Forces the next tick to redraw (visible signals or stream changed)."""
        self._version = None

    def tick(self) -> None:
        if self._plot.mode == PlotMode.ANALYSIS or self._store is None:
            return
        started = time.perf_counter()
        snapshot = self._store.overview(self._plot.visible_signal_ids(), self._version)
        if snapshot is None:
            return
        self._version = snapshot.version
        prepared = (
            DrawData(snapshot.time, snapshot.signals, snapshot.bounds)
            if snapshot.decimated
            else None
        )
        self._plot.show_packet(to_packet(snapshot), prepared)
        # Back off when a frame is expensive (many signals and samples), so the GUI thread
        # always has time left for input: spend at most about half of the time drawing.
        cost_ms = (time.perf_counter() - started) * 1000.0
        interval = max(self._base_interval_ms, round(2 * cost_ms))
        if interval != self.timer.interval():  # setInterval restarts the timer: a drift
            self.timer.setInterval(interval)

    def freeze(self) -> PlotPacketWithBounds | None:
        """Every sample of *all* signals, for analysis mode (so hidden ones can be shown)."""
        if self._store is None:
            return None
        snapshot = self._store.snapshot(None, None)
        return to_packet(snapshot) if snapshot is not None else None
