"""
Data Manager Module.
"""

from collections import deque
from typing import Deque, Dict, Optional, Union

import numpy as np

BufferValue = Union[float, int]


class SignalDataManager:
    """
    Manages telemetry data buffers, scaling configurations, and signal mappings.
    """

    def __init__(self, max_samples: int):
        self.max_samples = max_samples
        self.selected_motor = 0
        self.buffers: Dict[int, Dict[str, Deque[BufferValue]]] = {}
        self.scale: Dict[str, tuple] = {}
        self.field_map: Dict[str, str] = {}

    def configure(self, signals_cfg: dict):
        """Initializes buffers based on configuration."""
        self.buffers.clear()
        self.scale.clear()
        self.field_map.clear()

        for sig_id, sig in signals_cfg.items():
            self.field_map[sig_id] = sig["field"]
            yr = sig["y_range"]
            self.scale[sig_id] = (yr["min"], yr["max"])

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
        """Clears all data from all buffers (resets history)."""
        for motor_bufs in self.buffers.values():
            for buf in motor_bufs.values():
                buf.clear()

    def set_motor(self, motor_id: int):
        if motor_id in self.buffers:
            self.selected_motor = motor_id
            for buf in self.buffers[motor_id].values():
                buf.clear()

    def update_scale(self, sig_id: str, ymin: float, ymax: float):
        if sig_id in self.scale:
            self.scale[sig_id] = (ymin, ymax)

    def store_frame(self, decoded_frame: dict):
        motor = decoded_frame.get("motor")
        if motor not in self.buffers:
            return

        loop_cntr = decoded_frame.get("loopCntr", 0)
        motor_bufs = self.buffers[motor]

        motor_bufs["__loop__"].append(loop_cntr)

        for sig_id, field in self.field_map.items():
            # --- CRITICAL FIX: Use .get() default to 0.0 ---
            # Prevents KeyError if old packets arrive during switchover
            val = decoded_frame.get(field, 0.0)
            motor_bufs[sig_id].append(val)

    def get_plot_data(self, sample_period_s: float) -> Optional[dict]:
        if self.selected_motor not in self.buffers:
            return None

        motor_bufs = self.buffers[self.selected_motor]
        loops_cntr = motor_bufs["__loop__"]

        if len(loops_cntr) < 2:
            return None

        # Convert to numpy
        loop_cntr_arr = np.asarray(loops_cntr, dtype=float)
        time_axis = loop_cntr_arr * sample_period_s

        snapshot_raw = {}
        out_norm = {}

        for sig_id, buf in motor_bufs.items():
            if sig_id == "__loop__":
                continue

            arr = np.asarray(buf, dtype=float)

            # Sync check
            if len(arr) != len(time_axis):
                continue

            snapshot_raw[sig_id] = arr

            if sig_id in self.scale:
                ymin, ymax = self.scale[sig_id]
                scale = max(ymax - ymin, 1e-12)
                out_norm[sig_id] = np.clip((arr - ymin) / scale, 0.0, 1.0)

        return {
            "time": time_axis,
            "signals": out_norm,
            "raw": snapshot_raw,
        }
