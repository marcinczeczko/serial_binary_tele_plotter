"""
Sample storage: a thread-safe, versioned ring buffer per stream (ADR-0002, R2.4).

The writer (the reader thread) appends decoded frames. The GUI thread pulls snapshots at
its own frame rate. Nothing bulky crosses threads through the Qt event queue (P3), and a
snapshot copies only what will be drawn (P2).

Layout: one column-major (Fortran-order) matrix, `2 * capacity` rows by one column per
signal, plus a column of time ticks. Every sample row is written twice, at `i` and
`i + capacity`. The chronological window is then always the contiguous row range
`[head + capacity - count, head + capacity)`. So a snapshot is one `memcpy` per requested
column, and a batch of frames is a few slice assignments.

Time (R2.5): the stream's time field is turned into monotonic ticks at write time
(`TimeBase`: wrap, reset, gap markers). Seconds are applied at snapshot time
(`ticks x scale_s`), so correcting the scale re-times the whole history consistently.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np

from core.acquisition.lod import TARGET_BUCKETS, MinMaxLod
from core.acquisition.timebase import TimeBase, TimeBaseConfig, time_base_config
from core.types import DecodedFrame, SignalsConfig, StreamConfig


@dataclass
class Snapshot:
    """A consistent copy of the newest samples of some signals."""

    version: int
    time: np.ndarray
    signals: dict[str, np.ndarray]
    bounds: dict[str, tuple[float, float]] = field(default_factory=dict)
    """(min, max) of finite values per signal; signals without finite data are absent."""
    decimated: bool = False
    """Drawable min/max buckets (3 points each, see `overview`), not individual samples."""


class SampleStore:
    """
    Ring buffer of the latest `capacity` frames for one stream's signals.

    Thread safety: all public methods take the store's own lock and do bounded work while
    holding it (one frame batch, or one copy of the requested signals).
    """

    def __init__(self, capacity: int) -> None:
        self._lock = threading.Lock()
        self._capacity = max(int(capacity), 1)
        self._ids: list[str] = []  # column order
        self._fields: list[str] = []  # frame field per column
        self._unique_fields: list[str] = []
        self._float_dtype = np.dtype([])
        self._expand: list[int] | None = None
        self._col: dict[str, int] = {}
        self._time = TimeBase()
        self._scale_s = self._time.cfg.scale_s
        self._ticks = self._alloc_ticks()
        self._mat = self._alloc_matrix()
        self._lod = self._alloc_lod()
        self._rows = 0  # rows ever written (incl. ones a too-big batch skipped), for the LOD
        self._generation = 0  # bumps on clear/configure: row numbers restart
        self._head = 0  # next write position, 0..capacity-1
        self._count = 0  # valid samples, <= capacity
        self._version = 0  # bumps on every change visible to readers
        self.total_stored = 0  # monotonic, for rate reporting

    # --- configuration -----------------------------------------------------------------

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def version(self) -> int:
        return self._version

    def __len__(self) -> int:
        return self._count

    @property
    def time_scale_s(self) -> float:
        return self._scale_s

    @property
    def time_resets(self) -> int:
        """Times the time field went backwards (device reset) since the last clear."""
        return self._time.resets

    @property
    def time_gaps(self) -> int:
        """Gap markers inserted for lost frames since the last clear."""
        return self._time.gaps

    def configure(self, signals_cfg: SignalsConfig, time_cfg: TimeBaseConfig | None = None) -> None:
        """Replaces the signal set and time base; drops all samples."""
        with self._lock:
            self._time = TimeBase(time_cfg)
            self._scale_s = self._time.cfg.scale_s
            self._ids = list(signals_cfg)
            self._fields = [sig["field"] for sig in signals_cfg.values()]
            # Fast path of append_records: cast the *unique* fields once, then expand columns
            # (two signals may map to the same field).
            self._unique_fields = list(dict.fromkeys(self._fields))
            self._float_dtype = np.dtype(
                {
                    "names": [f"c{k}" for k in range(len(self._unique_fields))],
                    "formats": [np.float64] * len(self._unique_fields),
                }
            )
            self._expand = (
                [self._unique_fields.index(f) for f in self._fields]
                if len(self._unique_fields) != len(self._fields)
                else None
            )
            self._col = {sid: k for k, sid in enumerate(self._ids)}
            self._mat = self._alloc_matrix()
            self._lod = self._alloc_lod()
            self._reset_locked()

    def clear(self) -> None:
        with self._lock:
            self._reset_locked()

    def set_time_scale(self, scale_s: float) -> None:
        """Changes seconds per tick for the whole history (a correction, not a new segment)."""
        with self._lock:
            if scale_s > 0 and scale_s != self._scale_s:
                self._scale_s = float(scale_s)
                self._version += 1

    def resize(self, capacity: int) -> None:
        """Changes the capacity, keeping the newest samples that fit."""
        capacity = max(int(capacity), 1)
        with self._lock:
            if capacity == self._capacity:
                return
            keep = min(self._count, capacity)
            old_ticks = self._window_locked(self._ticks)[len(self) - keep :]
            old_rows = self._window_locked(self._mat)[len(self) - keep :]
            self._capacity = capacity
            self._ticks = self._alloc_ticks()
            self._mat = self._alloc_matrix()
            self._lod = self._alloc_lod()
            self._rows = 0
            self._head = 0
            self._count = 0
            self._write_locked(old_ticks, old_rows)
            self._version += 1

    # --- writer side -------------------------------------------------------------------

    def append(self, frames: Iterable[DecodedFrame]) -> int:
        """Stores frames in order (one lock acquisition per batch). Returns how many."""
        frames = list(frames)
        fields = self._fields
        # A missing field is "no data" (NaN, drawn as a gap), never 0 (C3).
        rows = [[frame.get(f, math.nan) for f in fields] for frame in frames]
        if not rows:
            return 0
        time_field = self._time.cfg.field
        raw = np.asarray([frame.get(time_field, math.nan) for frame in frames], dtype=np.float64)
        values = np.asarray(rows, dtype=np.float64).reshape(len(rows), len(fields))
        return self._store(fields, raw, values)

    def append_records(self, records: np.ndarray) -> int:
        """
        Stores a structured array from `RecordDecoder` (one record per frame).

        This is the fast path: one column copy per signal instead of a dict lookup per
        value. Fields a signal maps to but the records lack become NaN.
        """
        n = len(records)
        if not n:
            return 0
        names = records.dtype.names or ()
        time_field = self._time.cfg.field
        raw = (
            records[time_field].astype(np.float64) if time_field in names else np.full(n, math.nan)
        )
        fields = self._fields
        unique = self._unique_fields
        if unique and all(name in names for name in unique):
            # One C-level cast of the selected fields to all-float64, viewed as an n x U matrix.
            values = records[unique].astype(self._float_dtype).view(np.float64).reshape(n, -1)
            if self._expand is not None:
                values = values[:, self._expand]
        else:
            values = np.empty((n, len(fields)), dtype=np.float64)
            for k, name in enumerate(fields):
                values[:, k] = records[name] if name in names else math.nan
        return self._store(fields, raw, values)

    def _store(self, fields: list[str], raw: np.ndarray, values: np.ndarray) -> int:
        """Converts time and writes one prepared batch; the batch's rows are frames in order."""
        n = len(raw)
        with self._lock:
            if fields is not self._fields:
                return 0  # reconfigured while this batch was being prepared
            ticks, gap_index, gap_ticks = self._time.process(raw)
            if gap_index:
                # NaN rows break the drawn line where frames were lost or a segment restarted.
                ticks = np.insert(ticks, gap_index, gap_ticks)
                values = np.insert(values, gap_index, math.nan, axis=0)
            self._write_locked(ticks, values)
            self.total_stored += n
            self._version += 1
        return n

    # --- reader side -------------------------------------------------------------------

    def snapshot(
        self,
        signal_ids: Iterable[str] | None = None,
        since_version: int | None = None,
    ) -> Snapshot | None:
        """
        Copies the current window of `signal_ids` (all signals if None), every sample.

        Returns None when fewer than 2 samples are stored, or when nothing changed since
        `since_version`, so a caller polling at frame rate does no work while idle.
        Time is in seconds: the stream's monotonic ticks times its `scale_s`. It never
        decreases, even across counter wraps and device resets.
        """
        with self._lock:
            if self._count < 2 or (since_version is not None and since_version == self._version):
                return None
            wanted = self._ids if signal_ids is None else signal_ids
            ids = [sid for sid in wanted if sid in self._col]
            ticks = self._window_locked(self._ticks).copy()
            window = self._window_locked(self._mat)
            data = {sid: window[:, self._col[sid]].copy() for sid in ids}  # contiguous
            version = self._version
            scale_s = self._scale_s
        # Everything below works on private copies, outside the lock.
        bounds: dict[str, tuple[float, float]] = {}
        for sid, values in data.items():
            b = _finite_bounds(values, values)
            if b is not None:
                bounds[sid] = b
        ticks *= scale_s
        return Snapshot(version=version, time=ticks, signals=data, bounds=bounds)

    def overview(
        self,
        signal_ids: Iterable[str] | None = None,
        since_version: int | None = None,
    ) -> Snapshot | None:
        """
        The window of `signal_ids` ready to draw, from the incremental min/max level of
        detail (R3.4): 3 points per bucket (min, max, then max again or NaN where the bucket
        holds a gap), about `TARGET_BUCKETS` buckets whatever the capacity. That's what the
        live view pulls every frame. A window small enough to draw sample by sample comes
        back as a plain `snapshot()`. Same None rules as `snapshot()`.
        """
        with self._lock:
            if self._count < 2 or (since_version is not None and since_version == self._version):
                return None
            if self._lod.k == 1 or self._count <= 3 * TARGET_BUCKETS:
                small = True
            else:
                small = False
                wanted = self._ids if signal_ids is None else signal_ids
                cols = {sid: self._col[sid] for sid in wanted if sid in self._col}
                self._sync_lod_locked()
                t0, lo, hi, gap = self._lod.window(self._count)
                first_tick = float(self._window_locked(self._ticks)[0])
                version = self._version
                scale_s = self._scale_s
        if small:
            return self.snapshot(signal_ids, since_version)
        # The oldest bucket may reach back before the window; start it at the window.
        t0[0] = max(float(t0[0]), first_tick)
        time = np.repeat(t0 * scale_s, 3)
        signals: dict[str, np.ndarray] = {}
        bounds: dict[str, tuple[float, float]] = {}
        for sid, c in cols.items():
            y = np.empty(3 * len(t0))
            y[0::3] = lo[:, c]
            y[1::3] = hi[:, c]
            y[2::3] = np.where(gap[:, c], math.nan, hi[:, c])
            signals[sid] = y
            b = _finite_bounds(lo[:, c], hi[:, c])
            if b is not None:
                bounds[sid] = b
        return Snapshot(version, time, signals, bounds, decimated=True)

    def read_since(
        self, cursor: tuple[int, int] | None, signal_ids: Iterable[str]
    ) -> tuple[tuple[int, int], np.ndarray, dict[str, np.ndarray]]:
        """
        The samples written since `cursor` (every sample, gap markers included), with time
        in seconds, and the cursor for the next call. `None` starts at the newest sample
        (nothing returned yet). After a `clear()` the next call starts at the new data. If
        the ring already dropped some of the requested rows, you get the ones it still has.
        The trigger uses this to see every sample, not the live view's buckets.
        """
        with self._lock:
            now = (self._generation, self._rows)
            oldest = self._rows - self._count
            if cursor is None:
                start = self._rows
            elif cursor[0] != self._generation:
                start = oldest
            else:
                start = max(cursor[1], oldest)
            offset = start - oldest
            ticks = self._window_locked(self._ticks)[offset:].copy()
            window = self._window_locked(self._mat)
            data = {
                sid: window[offset:, self._col[sid]].copy()
                for sid in signal_ids
                if sid in self._col
            }
            scale_s = self._scale_s
        ticks *= scale_s
        return now, ticks, data

    def values_at(self, t_s: float, signal_ids: Iterable[str]) -> dict[str, float]:
        """
        Exact values at time `t_s` (linear between the two samples around it; clamped to
        the window). NaN where either neighbour is a gap or the signal has no data. The
        live cursor readout uses this, since live frames only hold min/max buckets.
        """
        with self._lock:
            if self._count < 2:
                return {}
            ticks = self._window_locked(self._ticks)
            x = min(max(t_s / self._scale_s, float(ticks[0])), float(ticks[-1]))
            i = int(np.searchsorted(ticks, x, side="right"))
            i = max(1, min(i, len(ticks) - 1))
            x0, x1 = float(ticks[i - 1]), float(ticks[i])
            rows = self._window_locked(self._mat)[i - 1 : i + 1].copy()
            cols = {sid: self._col[sid] for sid in signal_ids if sid in self._col}
        frac = (x - x0) / (x1 - x0) if x1 != x0 else 0.0
        return {
            sid: float(rows[0, c] + frac * (rows[1, c] - rows[0, c])) for sid, c in cols.items()
        }

    # --- internals ---------------------------------------------------------------------

    def _alloc_ticks(self) -> np.ndarray:
        return np.full(2 * self._capacity, math.nan, dtype=np.float64)

    def _alloc_matrix(self) -> np.ndarray:
        shape = (2 * self._capacity, len(self._ids))
        return np.full(shape, math.nan, dtype=np.float64, order="F")

    def _alloc_lod(self) -> MinMaxLod:
        return MinMaxLod(self._capacity, len(self._ids))

    def _write_locked(self, ticks: np.ndarray, values: np.ndarray) -> None:
        """Appends rows (both copies), wrapping at capacity; keeps the newest if too many."""
        cap = self._capacity
        n = len(ticks)
        self._rows += n  # the LOD catches up lazily, when a frame reads it
        if n > cap:
            ticks, values, n = ticks[-cap:], values[-cap:], cap
        done = 0
        while done < n:
            i = self._head
            k = min(n - done, cap - i)  # up to the wrap point
            for base in (i, i + cap):
                self._ticks[base : base + k] = ticks[done : done + k]
                self._mat[base : base + k] = values[done : done + k]
            self._head = (i + k) % cap
            done += k
        self._count = min(self._count + n, cap)

    def _sync_lod_locked(self) -> None:
        """
        Summarises the rows written since the last frame (~33 at 1 kHz and 30 FPS). Done
        here rather than per written batch, so the reader thread pays nothing for it.
        """
        lod = self._lod
        if lod.written == self._rows:
            return
        oldest = self._rows - self._count  # row number of the window's first sample
        start = max(lod.written, oldest)
        offset = start - oldest
        lod.add(
            self._window_locked(self._ticks)[offset:],
            self._window_locked(self._mat)[offset:],
            skipped=start - lod.written,
        )

    def _reset_locked(self) -> None:
        self._time.reset()
        self._lod.reset()
        self._rows = 0
        self._generation += 1
        self._head = 0
        self._count = 0
        self._version += 1

    def _window_locked(self, buf: np.ndarray) -> np.ndarray:
        """Chronological view (oldest..newest) of the valid samples; no copy."""
        end = self._head + self._capacity
        return buf[end - self._count : end]


def _finite_bounds(lo: np.ndarray, hi: np.ndarray) -> tuple[float, float] | None:
    """(min of `lo`, max of `hi`) ignoring NaN, or None when there's no finite value."""
    if not len(lo):
        return None
    low, high = float(np.fmin.reduce(lo)), float(np.fmax.reduce(hi))
    return (low, high) if math.isfinite(low) and math.isfinite(high) else None


class StreamStores:
    """
    One `SampleStore` per configured stream, keyed by stream key (R2.2).

    Shared between threads: the engine configures it and the reader thread appends. The
    GUI looks up the store of the stream it shows. `configure()` replaces every store, so
    holders must look stores up again afterwards (the engine signals `streams_configured`).
    """

    def __init__(self, capacity: int) -> None:
        self._lock = threading.Lock()
        self._capacity = max(int(capacity), 1)
        self._stores: dict[str, SampleStore] = {}

    def configure(self, streams: dict[str, StreamConfig]) -> None:
        """One store per stream key, with the stream's signals and time base."""
        stores = {}
        for key, cfg in streams.items():
            store = SampleStore(self._capacity)
            store.configure(cfg.get("signals", {}), time_base_config(cfg))
            stores[key] = store
        with self._lock:
            self._stores = stores

    def get(self, key: str | None) -> SampleStore | None:
        with self._lock:
            return self._stores.get(key) if key is not None else None

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._stores)

    def _all(self) -> list[SampleStore]:
        with self._lock:
            return list(self._stores.values())

    def resize(self, capacity: int) -> None:
        self._capacity = max(int(capacity), 1)
        for store in self._all():
            store.resize(self._capacity)

    def clear(self) -> None:
        for store in self._all():
            store.clear()

    @property
    def total_stored(self) -> int:
        return sum(store.total_stored for store in self._all())
