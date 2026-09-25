"""
Series helpers for drawing and reading out samples (R3.3, R3.4). Pure numpy, no Qt.

- `decimate`: min/max decimation to a pixel budget. A live window holds up to 100k
  samples per signal, but a lane is only ~1000 px wide. Handing pyqtgraph ~3 points per
  pixel instead of every sample is what keeps 34 signals at 30 FPS. Gaps survive it: a
  bucket that contains a NaN (lost frames, device reset) ends with a NaN, which breaks the
  line.
- `Interpolator`: values of many signals at one time. The bracketing samples are found
  once, then reused for every signal. A value next to a gap reads as NaN ("no data here"),
  never as a blend across the gap.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

POINTS_PER_BUCKET = 3  # min, max, and max again or NaN (a gap inside the bucket)


def decimate(
    time: np.ndarray, signals: dict[str, np.ndarray], buckets: int
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """
    Reduces every signal to `POINTS_PER_BUCKET` points per time bucket (bucket = equal
    sample count; the store's time is uniform between gaps, so that's ~equal width).

    Returns the inputs unchanged when they're already small enough to draw as they are.
    """
    n = len(time)
    buckets = max(int(buckets), 1)
    if n <= POINTS_PER_BUCKET * buckets:
        return time, signals
    k = n // buckets  # samples per bucket; the remainder joins the last bucket
    full = k * buckets
    starts = np.arange(buckets) * k
    t_out = np.repeat(time[starts], POINTS_PER_BUCKET)
    out: dict[str, np.ndarray] = {}
    for sid, y in signals.items():
        body = y[:full].reshape(buckets, k)
        # minimum/maximum propagate NaN, so one pass each finds both the extremes and the
        # buckets with a gap. Only those few buckets are rescanned, NaN-skipping.
        lo = np.minimum.reduce(body, axis=1)
        hi = np.maximum.reduce(body, axis=1)
        gap = np.isnan(lo)
        if gap.any():
            lo[gap] = np.fmin.reduce(body[gap], axis=1)
            hi[gap] = np.fmax.reduce(body[gap], axis=1)
        if full < n:  # fold the remainder into the last bucket
            tail = y[full:]
            lo[-1] = np.fmin(lo[-1], np.fmin.reduce(tail))
            hi[-1] = np.fmax(hi[-1], np.fmax.reduce(tail))
            gap[-1] |= bool(np.isnan(tail).any())
        y_out = np.empty(buckets * POINTS_PER_BUCKET)
        y_out[0::3] = lo
        y_out[1::3] = hi
        y_out[2::3] = np.where(gap, np.nan, hi)
        out[sid] = y_out
    return t_out, out


class Interpolator:
    """Linear interpolation of several signals at one time on a shared, sorted time axis."""

    def __init__(self, time: np.ndarray, t: float) -> None:
        n = len(time)
        self.valid = n >= 2
        if not self.valid:
            self._i, self._frac = 0, 0.0
            return
        t = float(np.clip(t, time[0], time[-1]))
        i = int(np.searchsorted(time, t, side="right"))
        i = max(1, min(i, n - 1))
        t0, t1 = float(time[i - 1]), float(time[i])
        self._i = i
        self._frac = (t - t0) / (t1 - t0) if t1 != t0 else 0.0

    def value(self, y: np.ndarray) -> float:
        """NaN when there's no data at that time (a gap, or a signal without data)."""
        if not self.valid or len(y) <= self._i:
            return math.nan
        v0, v1 = float(y[self._i - 1]), float(y[self._i])
        return v0 + self._frac * (v1 - v0)  # NaN if either neighbour is NaN


@dataclass(frozen=True)
class Readout:
    """
    The cursor readout (R3.3): its time, Δt to the anchor (None without one), each signal's
    value there (NaN: no data) and its Δ to the anchor's value. The plot computes it; the
    Signals panel shows it next to each signal (R6.2).
    """

    t: float
    dt: float | None
    values: dict[str, float]
    deltas: dict[str, float]


def finite_bounds(
    time: np.ndarray, y: np.ndarray, x_range: tuple[float, float] | None = None
) -> tuple[float, float] | None:
    """(min, max) of the finite values, optionally only where time is inside `x_range`."""
    if x_range is not None and len(time):
        lo_i, hi_i = (
            np.searchsorted(time, x_range[0], "left"),
            np.searchsorted(time, x_range[1], "right"),
        )
        y = y[lo_i:hi_i]
    if not len(y):
        return None
    lo, hi = float(np.fmin.reduce(y)), float(np.fmax.reduce(y))
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return None
    return lo, hi


@dataclass
class DrawData:
    """What a live frame hands pyqtgraph: decimated arrays and their (min, max) per signal."""

    time: np.ndarray
    signals: dict[str, np.ndarray]
    bounds: dict[str, tuple[float, float]]


def prepare_draw(time: np.ndarray, signals: dict[str, np.ndarray], buckets: int) -> DrawData:
    """
    Decimates a live packet for drawing (safe off the GUI thread: numpy only).

    The result never aliases the input: live packets are views into buffers the next
    snapshot overwrites. Bounds come from the decimated data, which keeps every bucket's
    extremes, so they equal the full data's at a fraction of the cost.
    """
    t_draw, draw = decimate(time, signals, buckets)
    if draw is signals:  # small enough to draw as is
        t_draw = time.copy()
        draw = {sid: y.copy() for sid, y in signals.items()}
    bounds = {sid: b for sid, y in draw.items() if (b := finite_bounds(t_draw, y)) is not None}
    return DrawData(t_draw, draw, bounds)
