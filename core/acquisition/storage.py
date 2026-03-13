"""
Data Manager Module.
Uses numpy arrays directly for buffers (no deque → array conversion).
"""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np

from core.protocol.constants import LOOP_CNTR_NAME
from core.types import DecodedFrame, PlotPacketWithRaw, SignalsConfig


class SignalDataManager:
    """
    Manages telemetry data in pre-allocated numpy arrays (circular buffer).
    No conversion overhead: get_plot_data returns array slices.
    """

    def __init__(self, max_samples: int):
        self.max_samples = max(int(max_samples), 1)
        # Pre-allocated arrays: [max_samples] each
        self._loop_arr: np.ndarray = np.zeros(self.max_samples, dtype=np.float64)
        self._signal_arrays: Dict[str, np.ndarray] = {}
        self._field_map: Dict[str, str] = {}
        # Circular buffer state
        self._write_index: int = 0
        self._count: int = 0

    def configure(self, signals_cfg: SignalsConfig) -> None:
        """Initializes buffers based on configuration."""
        self._signal_arrays.clear()
        self._field_map.clear()
        self._loop_arr = np.zeros(self.max_samples, dtype=np.float64)
        for sig_id, sig in signals_cfg.items():
            self._field_map[sig_id] = sig["field"]
            self._signal_arrays[sig_id] = np.zeros(self.max_samples, dtype=np.float64)
        self._write_index = 0
        self._count = 0

    def update_max_samples(self, max_samples: int) -> None:
        max_samples = int(max_samples)
        if max_samples == self.max_samples:
            return
        old_max = self.max_samples
        old_count = self._count
        old_write = self._write_index
        old_loop = self._loop_arr
        old_signals = dict(self._signal_arrays)
        self.max_samples = max(max_samples, 1)
        self._loop_arr = np.zeros(self.max_samples, dtype=np.float64)
        self._signal_arrays = {
            sid: np.zeros(self.max_samples, dtype=np.float64)
            for sid in old_signals
        }
        self._write_index = 0
        self._count = 0
        if old_count > 0:
            start = (old_write - old_count) % old_max
            indices = (np.arange(old_count) + start) % old_max
            copy_n = min(old_count, self.max_samples)
            # Keep the most recent copy_n samples
            src_idx = indices[-copy_n:]
            self._loop_arr[:copy_n] = old_loop[src_idx]
            for sid, arr in old_signals.items():
                self._signal_arrays[sid][:copy_n] = arr[src_idx]
            self._count = copy_n
            self._write_index = copy_n % self.max_samples

    def clear_all(self) -> None:
        self._write_index = 0
        self._count = 0

    def store_frame(self, decoded_frame: DecodedFrame) -> None:
        loop_cntr = float(decoded_frame.get(LOOP_CNTR_NAME, 0))
        idx = self._write_index
        self._loop_arr[idx] = loop_cntr
        for sig_id, field in self._field_map.items():
            val = decoded_frame.get(field, 0.0)
            self._signal_arrays[sig_id][idx] = float(val)
        self._write_index = (idx + 1) % self.max_samples
        self._count = min(self._count + 1, self.max_samples)

    def _logical_indices(self) -> np.ndarray:
        """Indices for valid samples in chronological order (oldest to newest)."""
        start = (self._write_index - self._count) % self.max_samples
        return (np.arange(self._count) + start) % self.max_samples

    def get_plot_data(self, sample_period_s: float) -> Optional[PlotPacketWithRaw]:
        if self._count < 2:
            return None
        idx = self._logical_indices()
        # Return copies so callers (e.g. last_packet) don't hold views into our buffer
        time_axis = (self._loop_arr[idx].copy() * sample_period_s)
        snapshot_raw: Dict[str, np.ndarray] = {
            sid: self._signal_arrays[sid][idx].copy()
            for sid in self._signal_arrays
        }
        return {
            "time": time_axis,
            "signals": snapshot_raw,
            "raw": snapshot_raw,
        }
