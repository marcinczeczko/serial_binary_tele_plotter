"""
Plot lanes: which signals share a Y axis, and how each lane's Y range behaves (R3.1, R3.2).

Lanes come from config that streams.json already had. `signals[*].group` names a
signal's lane, and the stream's `groups` object describes each lane:

    "groups": {
        "accel": {"label": "Accelerometer [g]", "order": 1,
                  "y_range": {"mode": "auto", "include_zero": false}}
    }

- A signal without a `group` goes in the default lane.
- A group used by signals but not described in `groups` gets its key as its label.
- `y_range.mode` is one of `Y_MODES`:
  - `auto` fits the data in view.
  - `auto-grow` only ever widens.
  - `manual` keeps `min`/`max`, or else the union of the lane's signals' own `y_range`.

Pure Python, no Qt: the widget in `telemetry_plot.py` applies these decisions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from core.config import Y_RANGE_MODES
from core.types import StreamConfig

Y_MODES = Y_RANGE_MODES
DEFAULT_LANE = ""
PAD_FRACTION = 0.05


@dataclass
class LaneSpec:
    key: str
    label: str
    order: float
    mode: str = "auto"
    manual: tuple[float, float] | None = None
    include_zero: bool = False


def _number(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _range_of(cfg: Any) -> tuple[float, float] | None:
    if not isinstance(cfg, dict):
        return None
    lo, hi = _number(cfg.get("min")), _number(cfg.get("max"))
    return (lo, hi) if lo is not None and hi is not None and lo < hi else None


def signal_lane(signal_cfg: Any) -> str:
    group = signal_cfg.get("group") if isinstance(signal_cfg, dict) else None
    return group if isinstance(group, str) else DEFAULT_LANE


def lane_layout(stream: StreamConfig | dict[str, Any]) -> tuple[list[LaneSpec], dict[str, str]]:
    """
    Returns (lanes in display order, signal id -> lane key). Only lanes that have at least
    one signal are listed. Invalid lane settings fall back to defaults; validation warns.
    """
    raw_groups: Any = stream.get("groups")
    groups: dict[str, Any] = raw_groups if isinstance(raw_groups, dict) else {}
    raw_signals: Any = stream.get("signals")
    signals: dict[str, Any] = raw_signals if isinstance(raw_signals, dict) else {}

    assignment = {sid: signal_lane(sig) for sid, sig in signals.items()}
    first_seen: dict[str, int] = {}
    for i, lane in enumerate(assignment.values()):
        first_seen.setdefault(lane, i)

    specs: list[LaneSpec] = []
    for key, seen in first_seen.items():
        group: Any = groups.get(key, {}) if key else {}
        group = group if isinstance(group, dict) else {}
        y_cfg: Any = group.get("y_range", {})
        y_cfg = y_cfg if isinstance(y_cfg, dict) else {}
        mode = y_cfg.get("mode", "auto")
        manual = _range_of(y_cfg)
        if manual is None:  # fall back to the union of the signals' own y_range
            member = [
                _range_of(signals[sid].get("y_range"))
                for sid, lane in assignment.items()
                if lane == key and isinstance(signals[sid], dict)
            ]
            ranges = [r for r in member if r is not None]
            if ranges:
                manual = (min(r[0] for r in ranges), max(r[1] for r in ranges))
        label = group.get("label")
        order = _number(group.get("order"))
        specs.append(
            LaneSpec(
                key=key,
                label=label if isinstance(label, str) else key,
                # Described lanes first (by order), then the rest as they appear.
                order=order if order is not None else 1e6 + seen,
                mode=mode if mode in Y_MODES else "auto",
                manual=manual,
                include_zero=y_cfg.get("include_zero") is True,
            )
        )
    specs.sort(key=lambda s: s.order)
    return specs, assignment


def target_y_range(
    mode: str,
    data: tuple[float, float] | None,
    grown: tuple[float, float] | None,
    include_zero: bool,
) -> tuple[float, float] | None:
    """
    The Y range a lane should show, or None to leave it alone (manual mode, or no data).

    `data` is the (min, max) of the lane's visible signals in view. `grown` is what an
    auto-grow lane has shown so far. The result is padded, and never zero-height.
    """
    if mode == "manual":
        return None
    bounds = data
    if mode == "auto-grow" and grown is not None:
        bounds = grown if bounds is None else (min(bounds[0], grown[0]), max(bounds[1], grown[1]))
    if bounds is None:
        return None
    lo, hi = bounds
    if include_zero:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    span = hi - lo
    if span <= 0:  # a flat signal: give it room instead of a zero-height axis
        span = max(abs(lo) * 0.1, 1e-3)
        lo, hi = lo - span / 2, hi + span / 2
    pad = PAD_FRACTION * (hi - lo)
    return lo - pad, hi + pad


def union(
    a: tuple[float, float] | None, b: tuple[float, float] | None
) -> tuple[float, float] | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a[0], b[0]), max(a[1], b[1])
