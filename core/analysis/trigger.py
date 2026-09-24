"""
Oscilloscope-style trigger capture (R4.4). Pure numpy, no Qt.

Arm a `TriggerSpec`, then `feed()` the trigger signal's new samples as they arrive. When
the signal crosses `level` on the chosen edge, the trigger fires at the interpolated
crossing time. The capture completes once `post_s` seconds after that have arrived. The
caller then freezes `[t - pre_s, t + post_s]` of every signal, for example around a
setpoint step. A NaN (gap marker, missing value) never counts as a crossing, so lost frames
or a device reset can't fire the trigger by themselves.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum, auto

import numpy as np

EDGES = ("rising", "falling", "either")


@dataclass(frozen=True)
class TriggerSpec:
    signal: str
    level: float
    edge: str = "rising"
    pre_s: float = 0.5
    post_s: float = 2.0


class TriggerState(Enum):
    IDLE = auto()
    ARMED = auto()  # waiting for the crossing
    FIRED = auto()  # crossed; waiting for post_s of data after it
    DONE = auto()  # capture complete (single shot: arm again for the next one)


def find_crossing(
    prev: float | None, t_prev: float | None, t: np.ndarray, y: np.ndarray, level: float, edge: str
) -> float | None:
    """
    Time of the first crossing of `level` in `y` (with `prev`, the sample before it, if
    any), linearly interpolated between the two samples around it. None if there's none.
    """
    if not len(y):
        return None
    ys = np.concatenate(([math.nan if prev is None else prev], y))
    ts = np.concatenate(([math.nan if t_prev is None else t_prev], t))
    a, b = ys[:-1], ys[1:]
    with np.errstate(invalid="ignore"):  # NaN compares False: never a crossing
        rising = (a < level) & (b >= level)
        falling = (a > level) & (b <= level)
    mask = {"rising": rising, "falling": falling}.get(edge, rising | falling)
    hits = np.flatnonzero(mask)
    if not len(hits):
        return None
    i = int(hits[0])
    t0, t1, y0, y1 = ts[i], ts[i + 1], a[i], b[i]
    if not math.isfinite(t0) or y1 == y0:
        return float(t1)
    return float(t0 + (level - y0) / (y1 - y0) * (t1 - t0))


class TriggerWatcher:
    def __init__(self) -> None:
        self.state = TriggerState.IDLE
        self.spec: TriggerSpec | None = None
        self.t_trigger: float | None = None
        self._prev: float | None = None
        self._t_prev: float | None = None

    def arm(self, spec: TriggerSpec) -> None:
        if spec.edge not in EDGES:
            raise ValueError(f"unknown edge {spec.edge!r} (known: {', '.join(EDGES)})")
        self.spec = spec
        self.state = TriggerState.ARMED
        self.t_trigger = None
        self._prev = self._t_prev = None

    def disarm(self) -> None:
        self.state = TriggerState.IDLE
        self.t_trigger = None

    def feed(self, t: np.ndarray, y: np.ndarray) -> bool:
        """
        New samples of the trigger signal, in order. Returns True once, when the capture
        is complete (`t_trigger` holds the crossing time).
        """
        spec = self.spec
        if spec is None or self.state in (TriggerState.IDLE, TriggerState.DONE):
            return False
        if self.state == TriggerState.ARMED:
            hit = find_crossing(self._prev, self._t_prev, t, y, spec.level, spec.edge)
            if hit is not None:
                self.t_trigger = hit
                self.state = TriggerState.FIRED
        if len(y):
            self._prev, self._t_prev = float(y[-1]), float(t[-1])
        if (
            self.state == TriggerState.FIRED
            and self.t_trigger is not None
            and len(t)
            and t[-1] >= self.t_trigger + spec.post_s
        ):
            self.state = TriggerState.DONE
            return True
        return False
