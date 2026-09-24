"""
Incremental min/max level of detail for the sample ring (R3.4).

Drawing a 100k-sample window means reducing it to about the plot's pixel width. Doing that
from scratch every frame (copy the window, then min/max it) cost 15-25 ms on the GUI thread
at 34 signals. Here the reduction happens as rows are written, on the reader thread: rows
are grouped into buckets of `k` consecutive rows, and each bucket keeps its min, max, whether
it holds a NaN (a gap marker), and its first sample's time. A frame then reads about
`TARGET_BUCKETS` buckets per signal: O(width), whatever the capacity.

Buckets are keyed by absolute row number, so writes only touch the newest bucket or two. The
oldest bucket in a window may still include rows the ring has already overwritten. That's at
most one pixel column at the left edge.

Pure numpy, no Qt. Not thread-safe on its own: `SampleStore` calls it under its lock.
"""

from __future__ import annotations

import math

import numpy as np

TARGET_BUCKETS = 1000  # about a plot's width in pixels; ~3 points each when drawn


class MinMaxLod:
    def __init__(self, capacity: int, columns: int, target_buckets: int = TARGET_BUCKETS) -> None:
        self.k = max(1, capacity // max(target_buckets, 1))  # rows per bucket
        self.slots = capacity // self.k + 2  # enough for a full window plus a partial bucket
        self.lo = np.full((self.slots, columns), math.nan)
        self.hi = np.full((self.slots, columns), math.nan)
        self.gap = np.zeros((self.slots, columns), dtype=bool)
        self.t0 = np.full(self.slots, math.nan)
        self._bucket = np.full(self.slots, -1, dtype=np.int64)  # which bucket a slot holds
        self.written = 0  # rows seen since the last reset (absolute row number)

    def reset(self) -> None:
        self.written = 0
        self._bucket[:] = -1

    def add(self, ticks: np.ndarray, values: np.ndarray, skipped: int = 0) -> None:
        """
        Adds rows in order. `skipped` rows came before these but were never stored (a batch
        bigger than the ring). They still advance the row numbering.
        """
        self.written += skipped
        k, n, r0 = self.k, len(ticks), self.written
        i = 0
        while i < n:
            row = r0 + i
            if row % k == 0 and n - i >= k:
                # Whole buckets: one vectorised reduction for all of them.
                m = (n - i) // k
                block = values[i : i + m * k].reshape(m, k, -1)
                buckets = row // k + np.arange(m)
                slots = buckets % self.slots
                self.lo[slots] = np.fmin.reduce(block, axis=1)
                self.hi[slots] = np.fmax.reduce(block, axis=1)
                self.gap[slots] = np.isnan(block).any(axis=1)
                self.t0[slots] = ticks[i : i + m * k : k]
                self._bucket[slots] = buckets
                i += m * k
                continue
            bucket = row // k
            end = min(n, (bucket + 1) * k - r0)
            seg = values[i:end]
            lo, hi = np.fmin.reduce(seg, axis=0), np.fmax.reduce(seg, axis=0)
            gap = np.isnan(seg).any(axis=0)
            slot = bucket % self.slots
            if self._bucket[slot] != bucket:  # the bucket's first rows: take them as they are
                self.lo[slot], self.hi[slot], self.gap[slot] = lo, hi, gap
                self.t0[slot] = ticks[i]
                self._bucket[slot] = bucket
            else:
                np.fmin(self.lo[slot], lo, out=self.lo[slot])
                np.fmax(self.hi[slot], hi, out=self.hi[slot])
                self.gap[slot] |= gap
            i = end
        self.written = r0 + n

    def window(self, count: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        The buckets covering the newest `count` rows, oldest first, as copies:
        (first tick per bucket, min, max, has-a-gap), with one row per bucket.
        """
        first = (self.written - count) // self.k
        last = (self.written - 1) // self.k
        slots = np.arange(first, last + 1) % self.slots
        return self.t0[slots], self.lo[slots], self.hi[slots], self.gap[slots]
