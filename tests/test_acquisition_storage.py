from __future__ import annotations

import threading
import time

import numpy as np

from core.acquisition.storage import SampleStore
from core.acquisition.timebase import TimeBaseConfig
from core.protocol.constants import LOOP_CNTR_NAME
from core.types import SignalsConfig

SIGNALS: SignalsConfig = {"sig_a": {"field": "a"}, "sig_b": {"field": "b"}}
UNIT_TIME = TimeBaseConfig(scale_s=1.0)  # time == loop_cntr, so assertions read naturally


def _frames(start: int, n: int) -> list[dict[str, float]]:
    return [{LOOP_CNTR_NAME: i, "a": i * 1.0, "b": i * 2.0} for i in range(start, start + n)]


def _store(capacity: int = 5) -> SampleStore:
    store = SampleStore(capacity)
    store.configure(SIGNALS, UNIT_TIME)
    return store


def test_snapshot_returns_chronological_window() -> None:
    store = _store()
    store.append(_frames(0, 4))
    store.set_time_scale(0.1)

    snap = store.snapshot()

    assert snap is not None
    assert np.allclose(snap.time, [0.0, 0.1, 0.2, 0.3])
    assert np.allclose(snap.signals["sig_a"], [0, 1, 2, 3])
    assert np.allclose(snap.signals["sig_b"], [0, 2, 4, 6])
    assert snap.bounds == {"sig_a": (0.0, 3.0), "sig_b": (0.0, 6.0)}


def test_wraparound_keeps_newest_in_order_for_any_batching() -> None:
    for batch in (1, 2, 3, 7, 13):
        store = _store(capacity=5)
        for start in range(0, 23, batch):
            store.append(_frames(start, min(batch, 23 - start)))
        snap = store.snapshot()
        assert snap is not None
        assert list(snap.time) == [18, 19, 20, 21, 22], batch
        assert len(store) == 5
        assert store.total_stored == 23


def test_batch_larger_than_capacity_keeps_the_tail() -> None:
    store = _store(capacity=4)
    store.append(_frames(0, 10))
    snap = store.snapshot()
    assert snap is not None
    assert list(snap.signals["sig_a"]) == [6, 7, 8, 9]


def test_snapshot_copies_only_requested_signals_and_is_a_copy() -> None:
    store = _store()
    store.append(_frames(0, 3))
    snap = store.snapshot(["sig_b", "unknown"])
    assert snap is not None
    assert list(snap.signals) == ["sig_b"]
    snap.signals["sig_b"][:] = -1
    again = store.snapshot(["sig_b"])
    assert again is not None
    assert list(again.signals["sig_b"]) == [0, 2, 4]


def test_version_lets_an_idle_poll_skip_work() -> None:
    store = _store()
    assert store.snapshot() is None  # fewer than 2 samples
    store.append(_frames(0, 3))
    first = store.snapshot()
    assert first is not None
    assert store.snapshot(since_version=first.version) is None
    store.append(_frames(3, 1))
    second = store.snapshot(since_version=first.version)
    assert second is not None and second.version > first.version


def test_missing_field_is_nan_not_zero() -> None:
    store = SampleStore(5)
    store.configure({"sig_a": {"field": "a"}, "ghost": {"field": "not_in_frame"}})
    store.append({LOOP_CNTR_NAME: i, "a": 1.0} for i in range(3))  # generators are fine

    snap = store.snapshot()

    assert snap is not None
    assert np.isnan(snap.signals["ghost"]).all()
    assert "ghost" not in snap.bounds
    assert snap.bounds["sig_a"] == (1.0, 1.0)


def test_resize_keeps_the_newest_samples() -> None:
    store = _store(capacity=5)
    store.append(_frames(0, 5))
    store.resize(3)
    snap = store.snapshot()
    assert snap is not None
    assert list(snap.signals["sig_a"]) == [2, 3, 4]
    store.resize(10)
    store.append(_frames(5, 2))
    snap = store.snapshot()
    assert snap is not None
    assert list(snap.signals["sig_a"]) == [2, 3, 4, 5, 6]


def test_clear_and_configure_drop_samples() -> None:
    store = _store()
    store.append(_frames(0, 3))
    store.clear()
    assert store.snapshot() is None
    store.append(_frames(0, 3))
    store.configure({"x": {"field": "a"}})
    assert store.snapshot() is None


def test_concurrent_writer_never_produces_torn_snapshots() -> None:
    """Every snapshot must be a gap-free run of consecutive frames with matching values."""
    store = SampleStore(500)
    store.configure(SIGNALS, UNIT_TIME)
    stop = threading.Event()

    def writer() -> None:
        i = 0
        while not stop.is_set():
            store.append(_frames(i, 7))
            i += 7
            time.sleep(0)  # yield the GIL so the reader gets to run

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        checked = 0
        version = None
        while checked < 50:
            snap = store.snapshot(["sig_a", "sig_b"], version)
            if snap is None:
                continue
            version = snap.version
            t = snap.time
            assert np.all(np.diff(t) == 1.0)
            assert np.array_equal(snap.signals["sig_a"], t)
            assert np.array_equal(snap.signals["sig_b"], 2 * t)
            checked += 1
    finally:
        stop.set()
        thread.join()
