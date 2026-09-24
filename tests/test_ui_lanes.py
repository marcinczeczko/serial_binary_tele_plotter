"""Lanes, Y-range policy, decimation and readout interpolation (R3.1-R3.4). No Qt needed."""

from __future__ import annotations

import math

import numpy as np
import pytest

from core.config import validate_stream
from ui.charts.lanes import DEFAULT_LANE, lane_layout, target_y_range, union
from ui.charts.series import POINTS_PER_BUCKET, Interpolator, decimate, finite_bounds


def _signals(**groups: str | None) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for sid, g in groups.items():
        out[sid] = {"field": sid} if g is None else {"field": sid, "group": g}
    return out


def test_lanes_follow_groups_and_their_order() -> None:
    specs, assignment = lane_layout(
        {
            "groups": {
                "fast": {"label": "Fast", "order": 2},
                "slow": {"label": "Slow [m]", "order": 1, "y_range": {"mode": "manual"}},
            },
            "signals": _signals(a="fast", b=None, c="slow", d="adhoc"),
        }
    )
    assert [(s.key, s.label) for s in specs] == [
        ("slow", "Slow [m]"),
        ("fast", "Fast"),
        (DEFAULT_LANE, ""),  # undescribed lanes follow, in order of appearance
        ("adhoc", "adhoc"),
    ]
    assert assignment == {"a": "fast", "b": DEFAULT_LANE, "c": "slow", "d": "adhoc"}
    assert specs[0].mode == "manual"


def test_lanes_without_signals_are_left_out_and_bad_settings_fall_back() -> None:
    specs, _ = lane_layout(
        {
            "groups": {"unused": {"label": "U"}, "g": {"order": "x", "y_range": {"mode": "zoom"}}},
            "signals": _signals(a="g"),
        }
    )
    assert [s.key for s in specs] == ["g"]
    assert specs[0].mode == "auto" and specs[0].label == "g"


def test_manual_bounds_come_from_the_group_else_from_its_signals() -> None:
    signals = _signals(a="g", b="g")
    signals["a"]["y_range"] = {"min": -2, "max": 1}
    signals["b"]["y_range"] = {"min": -1, "max": 5}
    specs, _ = lane_layout({"signals": signals})
    assert specs[0].manual == (-2.0, 5.0)
    specs, _ = lane_layout(
        {"groups": {"g": {"y_range": {"min": 0, "max": 10}}}, "signals": _signals(a="g")}
    )
    assert specs[0].manual == (0.0, 10.0)


def test_target_y_range_modes() -> None:
    # auto: fit (padded 5% of the span), never forcing zero in (A4: acc_z ~ 1 g)
    assert target_y_range("auto", (0.9, 1.1), None, False) == pytest.approx((0.89, 1.11))
    assert target_y_range("auto", (0.9, 1.1), None, True) == pytest.approx((-0.055, 1.155))
    # auto-grow: the union with what was shown
    assert target_y_range("auto-grow", (0.0, 1.0), (-5.0, 0.5), False) == pytest.approx((-5.3, 1.3))
    # manual never moves; no data leaves the range alone
    assert target_y_range("manual", (0.0, 1.0), None, False) is None
    assert target_y_range("auto", None, None, False) is None
    # a flat signal gets a visible, non-zero span
    lo, hi = target_y_range("auto", (3.0, 3.0), None, False)  # type: ignore[misc]
    assert lo < 3.0 < hi
    assert union(None, (1.0, 2.0)) == (1.0, 2.0) and union((0.0, 1.0), (2.0, 3.0)) == (0.0, 3.0)


def test_lane_settings_only_warn() -> None:
    stream = {
        "name": "S",
        "frame": {"stream_id": 1, "fields": [{"name": "loop_cntr", "type": "u32"}]},
        "groups": {
            "g": {"label": 3, "order": "1", "y_range": {"mode": "zoom", "min": 5, "max": 1}},
            "h": [],
        },
        "signals": {"a": {"field": "loop_cntr", "group": 7, "y_range": {"include_zero": 1}}},
    }
    problems = validate_stream("s", stream)
    assert {p.severity for p in problems} == {"warning"}
    assert [p.message for p in problems] == [
        "groups.g.label must be a string",
        "groups.g.order must be a number",
        "groups.g.y_range.mode 'zoom' is unknown (known: auto, auto-grow, manual)",
        "groups.g.y_range needs min < max",
        "groups.h must be an object",
        "signal 'a': group must be a string",
        "signal 'a'.y_range.include_zero must be true or false",
    ]


# --- series ---


def test_decimation_keeps_extremes_and_gaps_within_budget() -> None:
    t = np.arange(100_000) * 0.001
    y = np.sin(t)
    y[50_000] = 5.0  # a one-sample spike must survive
    y[70_000] = np.nan  # a gap marker must still break the line
    t_d, out = decimate(t, {"y": y}, 1000)

    y_d = out["y"]
    assert len(t_d) == len(y_d) == 1000 * POINTS_PER_BUCKET
    assert np.nanmax(y_d) == 5.0
    assert np.nanmin(y_d) == pytest.approx(y[~np.isnan(y)].min())
    assert np.isnan(y_d).sum() == 1  # exactly the bucket with the gap
    assert np.all(np.diff(t_d) >= 0)


def test_decimation_passes_small_inputs_through_and_folds_the_remainder() -> None:
    t = np.arange(10.0)
    signals = {"y": t * 2}
    assert decimate(t, signals, 100) == (t, signals)
    t = np.arange(1005.0)
    y = t.copy()
    _, out = decimate(t, {"y": y}, 100)
    assert np.nanmax(out["y"]) == 1004.0  # the last 5 samples are in the last bucket


def test_interpolator_reads_between_samples_and_not_across_gaps() -> None:
    t = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    y = np.array([0.0, 10.0, np.nan, 30.0, 40.0])
    assert Interpolator(t, 0.5).value(y) == pytest.approx(5.0)
    assert math.isnan(Interpolator(t, 1.5).value(y))  # next to the gap marker: no data
    assert Interpolator(t, 3.5).value(y) == pytest.approx(35.0)
    assert Interpolator(t, 99.0).value(y) == pytest.approx(40.0)  # clamped to the data
    assert math.isnan(Interpolator(t[:1], 0.0).value(y[:1]))  # one sample: nothing to read


def test_finite_bounds_in_an_x_range() -> None:
    t = np.arange(10.0)
    y = np.array([9.0, 1, 2, 3, np.nan, 5, 6, 7, 8, -9])
    assert finite_bounds(t, y) == (-9.0, 9.0)
    assert finite_bounds(t, y, (1.0, 5.0)) == (1.0, 5.0)
    assert finite_bounds(t, y, (4.0, 4.0)) is None  # only a gap in view
