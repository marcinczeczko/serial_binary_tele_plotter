"""
Trigger capture on the live data (R4.4): the Qt side of `core.analysis.trigger`.

While armed, the controller polls the shown stream's store at the live frame rate for
the trigger signal's new samples (every sample, via `SampleStore.read_since`, not the live
view's min/max buckets). When the capture completes, it cuts `[t - pre_s, t + post_s]` of
every signal out of the store and emits it. Single shot: arm again for the next capture.
"""

from __future__ import annotations

import numpy as np
from PyQt6 import QtCore

from core.acquisition.storage import SampleStore
from core.analysis.trigger import TriggerSpec, TriggerState, TriggerWatcher
from core.types import PlotPacketWithBounds

POLL_MS = 33


class TriggerController(QtCore.QObject):
    # (capture packet with every signal, trigger time in seconds, note for the user)
    captured = QtCore.pyqtSignal(object, float, str)
    state_changed = QtCore.pyqtSignal(str)  # "idle" | "armed" | "fired"

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.watcher = TriggerWatcher()
        self._store: SampleStore | None = None
        self._cursor: tuple[int, int] | None = None
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self.poll)

    @property
    def armed(self) -> bool:
        return self.watcher.state in (TriggerState.ARMED, TriggerState.FIRED)

    def set_store(self, store: SampleStore | None) -> None:
        """Another stream is shown: a pending capture is cancelled."""
        if store is not self._store:
            self._store = store
            self.disarm()

    def arm(self, spec: TriggerSpec) -> None:
        self.watcher.arm(spec)
        self._cursor = None  # only data from now on...
        if self._store is not None:
            (generation, row), _, _ = self._store.read_since(None, [])
            # ...plus the last sample before arming, so a crossing right after it counts.
            self._cursor = (generation, max(row - 1, 0))
        self.timer.start()
        self.state_changed.emit("armed")

    def disarm(self) -> None:
        was_armed = self.armed
        self.watcher.disarm()
        self.timer.stop()
        if was_armed:
            self.state_changed.emit("idle")

    def poll(self) -> None:
        spec, store = self.watcher.spec, self._store
        if spec is None or store is None or not self.armed:
            return
        before = self.watcher.state
        self._cursor, t, data = store.read_since(self._cursor, [spec.signal])
        y = data.get(spec.signal)
        if y is None:
            return
        done = self.watcher.feed(t, y)
        if self.watcher.state != before and self.watcher.state == TriggerState.FIRED:
            self.state_changed.emit("fired")
        if done and self.watcher.t_trigger is not None:
            self.timer.stop()
            self._emit_capture(store, spec, self.watcher.t_trigger)

    def _emit_capture(self, store: SampleStore, spec: TriggerSpec, t_trig: float) -> None:
        snapshot = store.snapshot(None, None)
        if snapshot is None:
            return
        t = snapshot.time
        lo = int(np.searchsorted(t, t_trig - spec.pre_s, side="left"))
        hi = int(np.searchsorted(t, t_trig + spec.post_s, side="right"))
        note = ""
        if lo == 0 and len(t) and t[0] > t_trig - spec.pre_s:
            note = (
                f"only {t_trig - float(t[0]):.3f} s before the trigger were still in the "
                "buffer (raise Samples to keep more)"
            )
        signals = {sid: y[lo:hi] for sid, y in snapshot.signals.items()}
        bounds: dict[str, tuple[float, float]] = {}
        for sid, y in signals.items():
            finite = y[np.isfinite(y)]
            if len(finite):
                bounds[sid] = (float(finite.min()), float(finite.max()))
        packet: PlotPacketWithBounds = {
            "time": t[lo:hi],
            "signals": signals,
            "signal_bounds": bounds,
        }
        self.state_changed.emit("idle")
        self.captured.emit(packet, t_trig, note)
