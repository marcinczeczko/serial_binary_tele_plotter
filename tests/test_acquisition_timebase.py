"""Per-stream time base (R2.5, C2): wrap, reset, gap markers, and how the store uses them."""

from __future__ import annotations

import numpy as np
import pytest

from core.acquisition.storage import SampleStore
from core.acquisition.timebase import TimeBase, TimeBaseConfig, time_base_config
from core.protocol.record_decoder import RecordDecoder
from core.types import SignalsConfig

U32 = 2.0**32


def _process(tb: TimeBase, raw: list[float]) -> tuple[list[float], list[int], list[float]]:
    ticks, gap_index, gap_ticks = tb.process(np.asarray(raw, dtype=np.float64))
    return ticks.tolist(), gap_index, gap_ticks


def test_consecutive_counter_keeps_its_values_as_ticks() -> None:
    tb = TimeBase()
    assert _process(tb, [100, 101, 102]) == ([100, 101, 102], [], [])
    assert _process(tb, [103]) == ([103], [], [])  # state carries across batches
    assert tb.gaps == tb.resets == 0


def test_u32_wrap_is_unwrapped_without_gap_or_reset() -> None:
    tb = TimeBase()
    ticks, gaps, _ = _process(tb, [U32 - 2, U32 - 1, 0, 1])
    assert ticks == [U32 - 2, U32 - 1, U32, U32 + 1]
    assert gaps == [] and tb.resets == 0


def test_small_counter_wraps_across_batches() -> None:
    tb = TimeBase(TimeBaseConfig(modulus=256.0))
    ticks = []
    for start in range(0, 600, 7):
        t, gaps, _ = _process(tb, [i % 256 for i in range(start, min(start + 7, 600))])
        assert gaps == []
        ticks += t
    assert ticks == list(range(600))


def test_counter_reset_starts_a_new_segment_after_a_gap_marker() -> None:
    tb = TimeBase()
    ticks, gap_index, gap_ticks = _process(tb, [100, 101, 102, 0, 1])
    # Time keeps growing: the new segment is placed after the old one, with a NaN marker.
    assert ticks == [100, 101, 102, 104, 105]
    assert gap_index == [3] and gap_ticks == [103]
    assert tb.resets == 1 and tb.gaps == 0


def test_reset_from_a_high_counter_is_not_mistaken_for_a_wrap() -> None:
    tb = TimeBase()
    ticks, gap_index, _ = _process(tb, [3_000_000_000, 3_000_000_001, 5])
    assert ticks == [3_000_000_000, 3_000_000_001, 3_000_000_003]
    assert gap_index == [2] and tb.resets == 1


def test_lost_frames_get_a_gap_marker_one_step_after_the_last_sample() -> None:
    tb = TimeBase()
    ticks, gap_index, gap_ticks = _process(tb, [0, 1, 5, 6])
    assert ticks == [0, 1, 5, 6]
    assert gap_index == [2] and gap_ticks == [2]
    _, gap_index, gap_ticks = _process(tb, [9])  # gap across a batch boundary
    assert gap_index == [0] and gap_ticks == [7]
    assert tb.gaps == 2 and tb.resets == 0


def test_repeated_value_is_neither_gap_nor_reset() -> None:
    tb = TimeBase()
    assert _process(tb, [5, 5, 6]) == ([5, 5, 6], [], [])


def test_timestamp_field_with_step_tolerates_jitter() -> None:
    tb = TimeBase(TimeBaseConfig(field="t_us", scale_s=1e-6, step=5000))
    ticks, gaps, _ = _process(tb, [0, 5100, 9900, 15000, 20050])
    assert ticks == [0, 5100, 9900, 15000, 20050] and gaps == []
    _, gaps, gap_ticks = _process(tb, [40000])  # three frames missing
    assert gaps == [0] and gap_ticks == [25050]


def test_float_time_field_never_wraps() -> None:
    tb = TimeBase(TimeBaseConfig(field="t", step=0.01, modulus=None))
    ticks, gaps, _ = _process(tb, [1.0, 1.01, 0.0])
    assert tb.resets == 1 and gaps == [2]
    assert ticks[2] == pytest.approx(1.03)


def test_reset_forgets_history() -> None:
    tb = TimeBase()
    _process(tb, [10, 11, 0])
    tb.reset()
    assert tb.resets == 0
    assert _process(tb, [0, 1]) == ([0, 1], [], [])


def test_time_base_config_defaults_and_field_type() -> None:
    cfg = time_base_config(
        {"frame": {"fields": [{"name": "loop_cntr", "type": "u16"}, {"name": "t", "type": "f32"}]}}
    )
    assert cfg == TimeBaseConfig(field="loop_cntr", scale_s=0.005, step=1.0, modulus=65536.0)
    cfg = time_base_config(
        {
            "frame": {"fields": [{"name": "t", "type": "f32"}]},
            "time": {"field": "t", "scale_s": 1, "step": 0.002},
        }
    )
    assert (cfg.field, cfg.scale_s, cfg.step, cfg.modulus) == ("t", 1.0, 0.002, None)
    assert cfg.period_s == pytest.approx(0.002)


# --- In the store ---

SIGNALS: SignalsConfig = {"a": {"field": "a"}}


def _store(time_cfg: TimeBaseConfig | None = None, capacity: int = 100) -> SampleStore:
    store = SampleStore(capacity)
    store.configure(SIGNALS, time_cfg or TimeBaseConfig(scale_s=1.0))
    return store


def _frames(counters: list[int]) -> list[dict[str, float]]:
    return [{"loop_cntr": c, "a": float(k)} for k, c in enumerate(counters)]


def test_store_time_stays_sorted_across_a_device_reset() -> None:
    store = _store()
    store.append(_frames([500, 501, 502, 0, 1, 2]))

    snap = store.snapshot()

    assert snap is not None
    assert np.all(np.diff(snap.time) > 0)  # the cursor readout's searchsorted needs this
    assert snap.time.tolist() == [500, 501, 502, 503, 504, 505, 506]
    values = snap.signals["a"]
    assert np.isnan(values[3])  # the marker breaks the line between the segments
    assert values[~np.isnan(values)].tolist() == [0, 1, 2, 3, 4, 5]
    assert store.time_resets == 1
    assert store.total_stored == 6  # frames, not markers


def test_store_marks_lost_frames_with_nan() -> None:
    store = _store()
    store.append(_frames([0, 1, 2, 10, 11]))
    snap = store.snapshot()
    assert snap is not None
    assert snap.time.tolist() == [0, 1, 2, 3, 10, 11]
    assert np.isnan(snap.signals["a"][3])
    assert store.time_gaps == 1
    assert snap.bounds["a"] == (0.0, 4.0)  # markers don't affect bounds


def test_changing_the_scale_retimes_the_whole_history() -> None:
    store = _store(TimeBaseConfig(scale_s=0.005))
    store.append(_frames([0, 1, 2]))
    first = store.snapshot()
    assert first is not None and first.time.tolist() == pytest.approx([0, 0.005, 0.01])

    store.set_time_scale(0.01)

    again = store.snapshot(since_version=first.version)  # a new version: the GUI redraws
    assert again is not None
    assert again.time.tolist() == pytest.approx([0, 0.01, 0.02])
    assert store.time_scale_s == 0.01


def test_clear_restarts_the_time_base() -> None:
    store = _store()
    store.append(_frames([100, 101]))
    store.clear()
    store.append(_frames([0, 1]))  # a new session: not a reset
    snap = store.snapshot()
    assert snap is not None and snap.time.tolist() == [0, 1]
    assert store.time_resets == 0


def test_records_use_the_configured_time_field() -> None:
    decoder = RecordDecoder(
        "little",
        [
            {"name": "loop_cntr", "type": "u32"},
            {"name": "t_us", "type": "u32"},
            {"name": "a", "type": "f32"},
        ],
    )
    records = np.zeros(4, dtype=decoder.dtype)
    records["loop_cntr"] = [0, 1, 2, 3]
    records["t_us"] = [1_000_000, 1_005_000, 1_010_000, 1_015_000]
    store = _store(TimeBaseConfig(field="t_us", scale_s=1e-6, step=5000))

    store.append_records(records)

    snap = store.snapshot()
    assert snap is not None
    assert snap.time.tolist() == pytest.approx([1.0, 1.005, 1.01, 1.015])


def test_small_counter_reset_is_not_mistaken_for_a_long_wrap() -> None:
    tb = TimeBase(TimeBaseConfig(modulus=256.0))
    _process(tb, [10, 11, 12, 0])  # through the wrap would be 244 frames lost: a reset
    assert tb.resets == 1
    _process(tb, [1, 2, 255, 0])  # 2 -> 255 is a gap, 255 -> 0 a wrap
    assert tb.resets == 1 and tb.gaps == 1
