"""
Step-response metrics for a captured setpoint step (R4.5). Pure numpy, no Qt.

Given a capture around a step (for example from the trigger), with time, the setpoint and
the measurement it drives, and the step time:
- **initial / final setpoint**: the median before the step, and over the last 20% of the
  capture.
- **rise time**: from 10% to 90% of the step, on the measurement.
- **overshoot**: the peak beyond the final value, in % of the step (0 if none).
- **settling time**: from the step until the measurement stays within ±`band` of the step
  around the final value (None if it never settles in the capture).
- **steady-state error**: final setpoint minus the mean measurement over the last 10%.

The measurement is normalised by the step, so a negative step works the same way.
Samples that are NaN (gaps) are ignored.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

SETTLE_BAND = 0.02  # ±2% of the step
MIN_STEP = 1e-9


@dataclass(frozen=True)
class StepMetrics:
    t_step: float
    initial: float
    final: float
    rise_time_s: float | None
    overshoot_pct: float
    settling_time_s: float | None
    steady_state_error: float

    @property
    def step(self) -> float:
        return self.final - self.initial

    def summary(self) -> str:
        def seconds(v: float | None) -> str:
            return f"{v:.3f} s" if v is not None else "n/a"

        return (
            f"step {self.initial:+.4g} → {self.final:+.4g} · rise {seconds(self.rise_time_s)} · "
            f"overshoot {self.overshoot_pct:.1f} % · settling {seconds(self.settling_time_s)} "
            f"(±{SETTLE_BAND * 100:.0f} %) · steady-state error {self.steady_state_error:+.4g}"
        )


def _first_time(t: np.ndarray, mask: np.ndarray) -> float | None:
    idx = np.flatnonzero(mask)
    return float(t[idx[0]]) if len(idx) else None


def step_metrics(
    t: np.ndarray,
    setpoint: np.ndarray,
    measurement: np.ndarray,
    t_step: float,
    band: float = SETTLE_BAND,
) -> StepMetrics | None:
    """The metrics, or None if there's no step (or no data on one side of it)."""
    ok = np.isfinite(t) & np.isfinite(setpoint) & np.isfinite(measurement)
    t, sp, m = t[ok], setpoint[ok], measurement[ok]
    before, after = t < t_step, t >= t_step
    if before.sum() < 1 or after.sum() < 5:
        return None
    t_end = float(t[-1])
    tail = t >= t_step + 0.8 * (t_end - t_step)
    initial = float(np.median(sp[before]))
    final = float(np.median(sp[tail])) if tail.any() else float(sp[-1])
    step = final - initial
    if abs(step) < MIN_STEP:
        return None

    m0 = float(np.mean(m[before]))
    y = (m[after] - m0) / step  # 0 before the step, 1 at the final value
    ta = t[after]
    t10 = _first_time(ta, y >= 0.1)
    t90 = _first_time(ta, y >= 0.9)
    rise = t90 - t10 if t10 is not None and t90 is not None else None
    overshoot = max(0.0, float(np.max(y)) - 1.0) * 100.0

    outside = np.flatnonzero(np.abs(y - 1.0) > band)
    if not len(outside):
        settling: float | None = 0.0
    elif outside[-1] == len(y) - 1:
        settling = None  # still outside the band at the end of the capture
    else:
        settling = float(ta[outside[-1] + 1]) - t_step

    last = t >= t_step + 0.9 * (t_end - t_step)
    sse = final - float(np.mean(m[last])) if last.any() else math.nan
    return StepMetrics(t_step, initial, final, rise, overshoot, settling, sse)
