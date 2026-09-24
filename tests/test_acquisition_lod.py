"""Incremental min/max level of detail and the store's live overview (R3.4)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from core.acquisition.lod import TARGET_BUCKETS, MinMaxLod
from core.acquisition.storage import SampleStore
from core.acquisition.timebase import TimeBaseConfig
from core.types import SignalsConfig

SIGNALS: SignalsConfig = {"a": {"field": "a"}, "b": {"field": "b"}}


def _rows(n: int, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    values = rng.normal(size=(n, 2))
    values[rng.random(n) < 0.01] = np.nan  # scattered gap markers
    return np.arange(n, dtype=float), values


def _brute(ticks: np.ndarray, values: np.ndarray, k: int, first_row: int) -> tuple[Any, ...]:
    """Buckets of absolute rows [b*k, (b+1)*k) over the rows we have, reduced from scratch."""
    rows = np.arange(first_row, first_row + len(ticks))
    buckets = rows // k
    out_t, out_lo, out_hi, out_gap = [], [], [], []
    for b in np.unique(buckets):
        sel = buckets == b
        out_t.append(ticks[sel][0])
        out_lo.append(np.fmin.reduce(values[sel], axis=0))
        out_hi.append(np.fmax.reduce(values[sel], axis=0))
        out_gap.append(np.isnan(values[sel]).any(axis=0))
    return np.array(out_t), np.array(out_lo), np.array(out_hi), np.array(out_gap)


@pytest.mark.parametrize("batch", [1, 7, 50, 333, 5000])
def test_incremental_buckets_equal_a_full_reduction(batch: int) -> None:
    capacity = 2000
    lod = MinMaxLod(capacity, 2, target_buckets=100)  # k = 20 rows per bucket
    ticks, values = _rows(5000)
    for i in range(0, 5000, batch):
        lod.add(ticks[i : i + batch], values[i : i + batch])

    count = capacity
    t0, lo, hi, gap = lod.window(count)
    # The oldest bucket may reach back before the window; compare whole buckets.
    first = (5000 - count) // lod.k * lod.k
    bt, blo, bhi, bgap = _brute(ticks[first:], values[first:], lod.k, first)
    assert np.array_equal(t0, bt)
    assert np.array_equal(lo, blo, equal_nan=True)
    assert np.array_equal(hi, bhi, equal_nan=True)
    assert np.array_equal(gap, bgap)


def test_skipped_rows_keep_the_numbering() -> None:
    lod = MinMaxLod(100, 2, target_buckets=10)  # k = 10
    ticks, values = _rows(250)
    lod.add(ticks[:5], values[:5])
    lod.add(ticks[150:], values[150:], skipped=145)  # a burst bigger than the ring
    assert lod.written == 250
    t0, lo, _, _ = lod.window(100)
    bt, blo, _, _ = _brute(ticks[150:], values[150:], 10, 150)
    assert np.array_equal(t0, bt) and np.array_equal(lo, blo, equal_nan=True)


# --- SampleStore.overview / values_at ---


def _store(capacity: int) -> SampleStore:
    store = SampleStore(capacity)
    store.configure(SIGNALS, TimeBaseConfig(scale_s=0.001))
    return store


def _frames(start: int, n: int) -> list[dict[str, float]]:
    return [
        {"loop_cntr": i, "a": math.sin(i / 500), "b": float(i % 7)} for i in range(start, start + n)
    ]


def test_small_windows_come_back_sample_by_sample() -> None:
    store = _store(100_000)
    store.append(_frames(0, 3 * TARGET_BUCKETS))
    snap = store.overview(["a"])
    assert snap is not None and not snap.decimated
    assert len(snap.time) == 3 * TARGET_BUCKETS


def test_large_window_overview_is_drawable_and_faithful() -> None:
    store = _store(100_000)
    frames = _frames(0, 120_000)
    frames[110_000]["loop_cntr"] = 110_010  # 9 frames lost: a gap marker in the window
    for i in range(0, 120_000, 997):  # lazy catch-up across many writes and a ring wrap
        store.append(frames[i : i + 997])
    full = store.snapshot(["a", "b"])
    snap = store.overview(["a", "b"])

    assert full is not None and snap is not None and snap.decimated
    buckets = len(snap.time) // 3
    assert TARGET_BUCKETS <= buckets <= TARGET_BUCKETS + 2
    assert np.all(np.diff(snap.time) >= 0)
    assert snap.time[0] == pytest.approx(full.time[0])  # starts at the window, not before
    for sid in ("a", "b"):
        assert snap.bounds[sid] == pytest.approx(full.bounds[sid])
    assert np.isnan(snap.signals["a"][2::3]).sum() == 1  # the gap survived, in one bucket
    assert snap.version == full.version
    assert store.overview(["a"], since_version=snap.version) is None  # idle: no work


def test_overview_after_clear_and_resize() -> None:
    store = _store(50_000)
    store.append(_frames(0, 50_000))
    store.resize(20_000)
    snap = store.overview(["a"])
    assert snap is not None and snap.decimated
    assert snap.time[0] == pytest.approx(30.0)  # the newest 20k kept: frames 30000..
    store.clear()
    store.append(_frames(0, 10_000))
    snap = store.overview(["a"])
    assert snap is not None and snap.time[0] == pytest.approx(0.0)
    assert snap.time[-1] <= 10.0


def test_values_at_is_exact_and_reads_gaps_as_na() -> None:
    store = _store(1000)
    frames = _frames(0, 100)
    frames[50]["a"] = math.nan
    store.append(frames)

    values = store.values_at(0.0205, ["a", "b", "unknown"])
    assert set(values) == {"a", "b"}
    assert values["a"] == pytest.approx(0.5 * (math.sin(20 / 500) + math.sin(21 / 500)))
    assert math.isnan(store.values_at(0.0495, ["a"])["a"])  # next to the missing value
    assert store.values_at(99.0, ["b"])["b"] == pytest.approx(99 % 7)  # clamped to the end
    assert _store(10).values_at(0.0, ["a"]) == {}
