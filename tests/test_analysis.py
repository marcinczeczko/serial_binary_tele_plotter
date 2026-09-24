"""Export (R4.3), trigger detection (R4.4) and step-response metrics (R4.5). No Qt."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

from core.acquisition.storage import SampleStore
from core.acquisition.timebase import TimeBaseConfig
from core.analysis.export import ExportError, export_table, parquet_available, select_rows
from core.analysis.step_response import step_metrics
from core.analysis.trigger import TriggerSpec, TriggerWatcher, find_crossing

# --- export ---


def test_csv_has_time_first_and_empty_fields_for_missing_values(tmp_path: Path) -> None:
    t = np.array([0.0, 0.005, 0.010, 0.015])
    cols = {
        "speed": np.array([0.25, np.nan, np.nan, 0.5]),  # row 1 and 2: gap markers
        "count": np.array([1.0, np.nan, np.nan, np.nan]),
    }
    cols["count"][2] = 3.0  # row 2 is a real sample with one missing value

    rows = export_table(tmp_path / "s.csv", t, cols)

    assert rows == 3
    lines = (tmp_path / "s.csv").read_text().splitlines()
    assert lines == ["time_s,speed,count", "0,0.25,1", "0.01,,3", "0.015,0.5,"]


def test_export_selection_and_errors(tmp_path: Path) -> None:
    t = np.arange(10) * 0.1
    cols = {"a": np.arange(10.0)}
    assert export_table(tmp_path / "s.csv", t, cols, (0.25, 0.55)) == 3  # 0.3, 0.4, 0.5
    with pytest.raises(ExportError, match="unknown export format"):
        export_table(tmp_path / "s.xlsx", t, cols)
    kept_t, kept = select_rows(t, {"a": np.full(10, np.nan)})
    assert len(kept_t) == 0 and len(kept["a"]) == 0


def test_parquet_export_without_pyarrow_is_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "pyarrow", None)  # import fails as if not installed
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", None)
    assert not parquet_available()
    with pytest.raises(ExportError, match="pyarrow"):
        export_table(tmp_path / "s.parquet", np.array([0.0]), {"a": np.array([1.0])})


def test_parquet_export_writes_nulls_for_missing_values(tmp_path: Path) -> None:
    pq = pytest.importorskip("pyarrow.parquet")  # the optional `parquet` extra
    t = np.array([0.0, 1.0])
    cols = {"a": np.array([1.0, np.nan]), "b": np.array([2.0, 3.0])}
    assert export_table(tmp_path / "s.parquet", t, cols) == 2
    table = pq.read_table(tmp_path / "s.parquet")
    assert table.column_names == ["time_s", "a", "b"]
    assert table.column("a").to_pylist() == [1.0, None]


# --- trigger ---


def test_find_crossing_edges_and_interpolation() -> None:
    t = np.array([1.0, 2.0, 3.0, 4.0])
    y = np.array([0.0, 2.0, 1.0, -1.0])
    assert find_crossing(None, None, t, y, 1.0, "rising") == pytest.approx(1.5)
    assert find_crossing(None, None, t, y, 0.0, "falling") == pytest.approx(3.5)
    assert find_crossing(None, None, t, y, 1.5, "either") == pytest.approx(1.75)
    assert find_crossing(None, None, t, y, 5.0, "rising") is None
    # the sample before the block counts: -1 -> 0 at the boundary crosses -0.5
    assert find_crossing(-1.0, 0.0, t, y, -0.5, "rising") == pytest.approx(0.5)


def test_a_gap_never_fires_the_trigger() -> None:
    t = np.arange(5.0)
    y = np.array([0.0, np.nan, 5.0, np.nan, 0.0])
    assert find_crossing(None, None, t, y, 1.0, "either") is None


def test_watcher_fires_then_completes_after_post_time() -> None:
    watcher = TriggerWatcher()
    watcher.arm(TriggerSpec("sp", level=0.5, edge="rising", pre_s=0.1, post_s=1.0))
    assert not watcher.feed(np.array([0.0, 0.1]), np.array([0.0, 0.0]))
    assert not watcher.feed(np.array([0.2, 0.3]), np.array([1.0, 1.0]))  # crosses at 0.15
    assert watcher.state.name == "FIRED"
    assert watcher.t_trigger == pytest.approx(0.15)
    assert not watcher.feed(np.array([1.0]), np.array([1.0]))  # not 1 s after yet
    assert watcher.feed(np.array([1.2]), np.array([1.0]))
    assert watcher.state.name == "DONE"
    assert not watcher.feed(np.array([1.3]), np.array([0.0]))  # single shot
    watcher.disarm()
    assert watcher.state.name == "IDLE"
    with pytest.raises(ValueError, match="unknown edge"):
        watcher.arm(TriggerSpec("sp", 0.0, edge="sideways"))


def test_store_read_since_sees_every_new_sample() -> None:
    store = SampleStore(100)
    store.configure({"a": {"field": "a"}}, TimeBaseConfig(scale_s=0.01))
    store.append([{"loop_cntr": i, "a": float(i)} for i in range(10)])
    cursor, t, data = store.read_since(None, ["a"])
    assert len(t) == 0  # from now on
    store.append([{"loop_cntr": i, "a": float(i)} for i in range(10, 13)])
    cursor, t, data = store.read_since(cursor, ["a"])
    assert t.tolist() == pytest.approx([0.10, 0.11, 0.12]) and data["a"].tolist() == [10, 11, 12]
    store.append([{"loop_cntr": i, "a": float(i)} for i in range(13, 250)])  # overflows
    cursor, t, data = store.read_since(cursor, ["a"])
    assert len(t) == 100 and data["a"][-1] == 249  # what the ring still has
    store.clear()
    store.append([{"loop_cntr": 0, "a": 7.0}])
    _, t, data = store.read_since(cursor, ["a"])  # a new session: from its start
    assert data["a"].tolist() == [7.0]


# --- step response ---


def _first_order(
    tau: float, step: float = 2.0, n: int = 4000, dt: float = 0.001
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    t = np.arange(n) * dt - 0.5  # the step is at t = 0
    sp = np.where(t >= 0, 1.0 + step, 1.0)
    m = np.where(t >= 0, 1.0 + step * (1 - np.exp(-np.clip(t, 0, None) / tau)), 1.0)
    return t, sp, m


def test_first_order_step_metrics() -> None:
    t, sp, m = _first_order(tau=0.2)
    metrics = step_metrics(t, sp, m, 0.0)

    assert metrics is not None
    assert (metrics.initial, metrics.final, metrics.step) == (1.0, 3.0, 2.0)
    assert metrics.rise_time_s == pytest.approx(0.2 * math.log(9), abs=2e-3)  # 10 -> 90 %
    assert metrics.overshoot_pct == 0.0
    assert metrics.settling_time_s == pytest.approx(0.2 * math.log(50), abs=2e-3)  # 2 % band
    assert abs(metrics.steady_state_error) < 1e-3
    assert "rise 0.439 s" in metrics.summary()


def test_overshoot_negative_step_and_no_step() -> None:
    t = np.arange(3000) * 0.001 - 0.5
    zeta, wn = 0.3, 20.0
    wd = wn * math.sqrt(1 - zeta**2)
    tp = np.clip(t, 0, None)
    resp = 1 - np.exp(-zeta * wn * tp) * (
        np.cos(wd * tp) + zeta / math.sqrt(1 - zeta**2) * np.sin(wd * tp)
    )
    sp = np.where(t >= 0, -1.0, 0.0)  # a negative step
    m = np.where(t >= 0, -resp, 0.0)
    metrics = step_metrics(t, sp, m, 0.0)
    assert metrics is not None and metrics.step == -1.0
    expected = 100 * math.exp(-zeta * math.pi / math.sqrt(1 - zeta**2))
    assert metrics.overshoot_pct == pytest.approx(expected, rel=0.02)

    flat = np.zeros_like(t)
    assert step_metrics(t, flat, flat, 0.0) is None
    assert step_metrics(t, sp, m, -10.0) is None  # nothing before the step


def test_not_settled_within_the_capture() -> None:
    t, sp, m = _first_order(tau=5.0)
    metrics = step_metrics(t, sp, m, 0.0)
    assert metrics is not None and metrics.settling_time_s is None
    assert "settling n/a" in metrics.summary()
