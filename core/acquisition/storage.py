"""
Sample storage: a thread-safe, versioned ring buffer per stream (ADR-0002, R2.4).

The writer (the reader thread) appends decoded frames. The GUI thread pulls snapshots at
its own frame rate. Nothing bulky crosses threads through the Qt event queue (P3), and a
snapshot copies only what will be drawn (P2).

Layout: one column-major (Fortran-order) matrix, `2 * capacity` rows by one column per
signal, plus the loop counter. Every sample row is written twice, at `i` and
`i + capacity`. The chronological window is then always the contiguous row range
`[head + capacity - count, head + capacity)`. So a snapshot is one `memcpy` per requested
column, and a batch of frames is a few slice assignments.
"""

from __future__ import annotations

import math
import threading
import warnings
from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np

from core.protocol.constants import LOOP_CNTR_NAME
from core.types import DecodedFrame, SignalsConfig


@dataclass
class Snapshot:
    """A consistent copy of the newest samples of some signals."""

    version: int
    time: np.ndarray
    signals: dict[str, np.ndarray]
    bounds: dict[str, tuple[float, float]] = field(default_factory=dict)
    """(min, max) of finite values per signal; signals without finite data are absent."""


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
        self._col: dict[str, int] = {}
        self._loop = self._alloc_loop()
        self._mat = self._alloc_matrix()
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

    def configure(self, signals_cfg: SignalsConfig) -> None:
        """Replaces the signal set; drops all samples."""
        with self._lock:
            self._ids = list(signals_cfg)
            self._fields = [sig["field"] for sig in signals_cfg.values()]
            self._col = {sid: k for k, sid in enumerate(self._ids)}
            self._mat = self._alloc_matrix()
            self._reset_locked()

    def clear(self) -> None:
        with self._lock:
            self._reset_locked()

    def resize(self, capacity: int) -> None:
        """Changes the capacity, keeping the newest samples that fit."""
        capacity = max(int(capacity), 1)
        with self._lock:
            if capacity == self._capacity:
                return
            keep = min(self._count, capacity)
            old_loop = self._window_locked(self._loop)[len(self) - keep :]
            old_rows = self._window_locked(self._mat)[len(self) - keep :]
            self._capacity = capacity
            self._loop = self._alloc_loop()
            self._mat = self._alloc_matrix()
            self._head = 0
            self._count = 0
            self._write_locked(old_loop, old_rows)
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
        counters = [frame.get(LOOP_CNTR_NAME, math.nan) for frame in frames]
        values = np.asarray(rows, dtype=np.float64).reshape(len(rows), len(fields))
        loop = np.asarray(counters, dtype=np.float64)
        with self._lock:
            if len(fields) != self._mat.shape[1]:
                return 0  # reconfigured while this batch was being prepared
            self._write_locked(loop, values)
            self.total_stored += len(rows)
            self._version += 1
        return len(rows)

    # --- reader side -------------------------------------------------------------------

    def snapshot(
        self,
        signal_ids: Iterable[str] | None = None,
        since_version: int | None = None,
        sample_period_s: float = 1.0,
    ) -> Snapshot | None:
        """
        Copies the current window of `signal_ids` (all signals if None).

        Returns None when fewer than 2 samples are stored, or when nothing changed since
        `since_version`, so a caller polling at frame rate does no work while idle.
        Time is `loop_cntr * sample_period_s` (per-stream time bases arrive with R2.5).
        """
        with self._lock:
            if self._count < 2 or (since_version is not None and since_version == self._version):
                return None
            wanted = self._ids if signal_ids is None else signal_ids
            ids = [sid for sid in wanted if sid in self._col]
            loop = self._window_locked(self._loop).copy()
            window = self._window_locked(self._mat)
            data = {sid: window[:, self._col[sid]].copy() for sid in ids}  # contiguous
            version = self._version
        # Everything below works on private copies, outside the lock.
        bounds: dict[str, tuple[float, float]] = {}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN signals
            for sid, values in data.items():
                lo, hi = float(np.nanmin(values)), float(np.nanmax(values))
                if math.isfinite(lo) and math.isfinite(hi):
                    bounds[sid] = (lo, hi)
        return Snapshot(version=version, time=loop * sample_period_s, signals=data, bounds=bounds)

    # --- internals ---------------------------------------------------------------------

    def _alloc_loop(self) -> np.ndarray:
        return np.full(2 * self._capacity, math.nan, dtype=np.float64)

    def _alloc_matrix(self) -> np.ndarray:
        shape = (2 * self._capacity, len(self._ids))
        return np.full(shape, math.nan, dtype=np.float64, order="F")

    def _write_locked(self, loop: np.ndarray, values: np.ndarray) -> None:
        """Appends rows (both copies), wrapping at capacity; keeps the newest if too many."""
        cap = self._capacity
        n = len(loop)
        if n > cap:
            loop, values, n = loop[-cap:], values[-cap:], cap
        done = 0
        while done < n:
            i = self._head
            k = min(n - done, cap - i)  # up to the wrap point
            for base in (i, i + cap):
                self._loop[base : base + k] = loop[done : done + k]
                self._mat[base : base + k] = values[done : done + k]
            self._head = (i + k) % cap
            done += k
        self._count = min(self._count + n, cap)

    def _reset_locked(self) -> None:
        self._head = 0
        self._count = 0
        self._version += 1

    def _window_locked(self, buf: np.ndarray) -> np.ndarray:
        """Chronological view (oldest..newest) of the valid samples; no copy."""
        end = self._head + self._capacity
        return buf[end - self._count : end]
