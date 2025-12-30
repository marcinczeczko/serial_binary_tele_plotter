"""
Data Manager Module.
FIXED: Removed normalization logic. Returns RAW values in 'signals' key.
"""

from collections import deque
from typing import Deque, Dict, Optional, Union

import numpy as np

BufferValue = Union[float, int]


class SignalDataManager:
    """
    Manages telemetry data buffers.
    CLEANED: No scaling, no normalization. Pure raw data storage.
    """

    def __init__(self, max_samples: int):
        self.max_samples = max_samples
        self.selected_motor = 0
        self.buffers: Dict[int, Dict[str, Deque[BufferValue]]] = {}
        # field_map mapuje nazwę sygnału (np. "setpoint") na pole w ramce C++
        self.field_map: Dict[str, str] = {}

    def configure(self, signals_cfg: dict):
        """Initializes buffers based on configuration."""
        self.buffers.clear()
        self.field_map.clear()

        for sig_id, sig in signals_cfg.items():
            self.field_map[sig_id] = sig["field"]

        # Create buffers for Motors 0 and 1
        for motor_id in (0, 1):
            motor_bufs = {"__loop__": deque(maxlen=self.max_samples)}
            for sig_id in signals_cfg.keys():
                motor_bufs[sig_id] = deque(maxlen=self.max_samples)
            self.buffers[motor_id] = motor_bufs

    def update_max_samples(self, max_samples: int):
        self.max_samples = max_samples
        for motor_bufs in self.buffers.values():
            for sig_id, old_buf in motor_bufs.items():
                motor_bufs[sig_id] = deque(old_buf, maxlen=max_samples)

    def clear_all(self):
        for motor_bufs in self.buffers.values():
            for buf in motor_bufs.values():
                buf.clear()

    def set_motor(self, motor_id: int):
        if motor_id in self.buffers:
            self.selected_motor = motor_id
            for buf in self.buffers[motor_id].values():
                buf.clear()

    def store_frame(self, decoded_frame: dict):
        motor = decoded_frame.get("motor", 0)
        if motor not in self.buffers:
            return

        loop_cntr = decoded_frame.get("loopCntr", 0)
        motor_bufs = self.buffers[motor]

        motor_bufs["__loop__"].append(loop_cntr)

        for sig_id, field in self.field_map.items():
            val = decoded_frame.get(field, 0.0)
            motor_bufs[sig_id].append(val)

    def get_plot_data(self, sample_period_s: float) -> Optional[dict]:
        if self.selected_motor not in self.buffers:
            return None

        motor_bufs = self.buffers[self.selected_motor]
        loops_cntr = motor_bufs["__loop__"]

        if len(loops_cntr) < 2:
            return None

        loop_cntr_arr = np.asarray(loops_cntr, dtype=float)
        time_axis = loop_cntr_arr * sample_period_s

        snapshot_raw = {}

        for sig_id, buf in motor_bufs.items():
            if sig_id == "__loop__":
                continue

            arr = np.asarray(buf, dtype=float)
            if len(arr) != len(time_axis):
                continue

            snapshot_raw[sig_id] = arr
            # USUNIĘTO: obliczanie out_norm

        return {
            "time": time_axis,
            "signals": snapshot_raw,  # Teraz signals to to samo co raw
            "raw": snapshot_raw,
        }
