"""
Data Manager Module.
FIXED: Removed normalization logic. Returns RAW values in 'signals' key.
"""

from collections import deque
from typing import Deque, Dict, Optional, Union

import numpy as np

from core.protocol.constants import LOOP_CNTR_NAME

BufferValue = Union[float, int]


class SignalDataManager:
    """
    Manages telemetry data buffers.
    CLEANED: No scaling, no normalization. Pure raw data storage.
    """

    def __init__(self, max_samples: int):
        self.max_samples = max_samples
        self.buffers: Dict[str, Deque[BufferValue]] = {}
        self.field_map: Dict[str, str] = {}

    def configure(self, signals_cfg: dict):
        """Initializes buffers based on configuration."""
        self.buffers.clear()
        self.field_map.clear()

        self.buffers["__loop__"] = deque(maxlen=self.max_samples)

        for sig_id, sig in signals_cfg.items():
            self.field_map[sig_id] = sig["field"]
            self.buffers[sig_id] = deque(maxlen=self.max_samples)

    def update_max_samples(self, max_samples: int):
        self.max_samples = max_samples
        for sig_id, old_buf in self.buffers.items():
            self.buffers[sig_id] = deque(old_buf, maxlen=max_samples)

    def clear_all(self):
        for buf in self.buffers.values():
            buf.clear()

    def store_frame(self, decoded_frame: dict):
        loop_cntr = decoded_frame.get(LOOP_CNTR_NAME, 0)

        self.buffers["__loop__"].append(loop_cntr)

        for sig_id, field in self.field_map.items():
            if field in decoded_frame:
                self.buffers[sig_id].append(decoded_frame[field])
            else:
                # keep timeline consisten
                self.buffers[sig_id].append(0.0)

    def get_plot_data(self, sample_period_s: float) -> Optional[dict]:
        loop_buf = self.buffers.get("__loop__")
        if loop_buf is None or len(loop_buf) < 2:
            return None

        loop_cntr_arr = np.asarray(loop_buf, dtype=float)
        time_axis = loop_cntr_arr * sample_period_s

        snapshot_raw = {}

        for sig_id, buf in self.buffers.items():
            if sig_id == "__loop__":
                continue

            arr = np.asarray(buf, dtype=float)
            if len(arr) != len(time_axis):
                continue

            snapshot_raw[sig_id] = arr

        return {
            "time": time_axis,
            "signals": snapshot_raw,
            "raw": snapshot_raw,
        }
