from collections import deque
from typing import Deque, Dict, Optional, Union

import numpy as np

# Typ pomocniczy
BufferValue = Union[float, int]


class SignalDataManager:
    """
    Helper class to encapsulate data buffering, scaling, and signal mapping logic.
    This reduces the complexity and attribute count of the main TelemetryWorker.
    """

    def __init__(self, max_samples: int):
        self.max_samples = max_samples
        self.selected_motor = 0

        # buffers[motor_id][signal_id] -> deque
        self.buffers: Dict[int, Dict[str, Deque[BufferValue]]] = {}
        # signal_id -> (min, max)
        self.scale: Dict[str, tuple] = {}
        # signal_id -> frame field name
        self.field_map: Dict[str, str] = {}

    def configure(self, signals_cfg: dict):
        """Initializes buffers and mappings based on config."""
        self.buffers.clear()
        self.scale.clear()
        self.field_map.clear()

        # Load signal configurations
        for sig_id, sig in signals_cfg.items():
            self.field_map[sig_id] = sig["field"]
            yr = sig["y_range"]
            self.scale[sig_id] = (yr["min"], yr["max"])

        # Prepare buffers for motors 0 (Left) and 1 (Right)
        for motor_id in (0, 1):
            motor_bufs = {"__loop__": deque(maxlen=self.max_samples)}
            for sig_id in signals_cfg.keys():
                motor_bufs[sig_id] = deque(maxlen=self.max_samples)
            self.buffers[motor_id] = motor_bufs

    def update_max_samples(self, max_samples: int):
        """Resizes all existing buffers to the new maximum sample count."""
        self.max_samples = max_samples
        for motor_bufs in self.buffers.values():
            for sig_id, old_buf in motor_bufs.items():
                motor_bufs[sig_id] = deque(old_buf, maxlen=max_samples)

    def clear_all(self):
        """Clears all data from all buffers."""
        for motor_bufs in self.buffers.values():
            for buf in motor_bufs.values():
                buf.clear()

    def set_motor(self, motor_id: int):
        """Sets the active motor and clears buffers to prevent data mixing."""
        if motor_id in self.buffers:
            self.selected_motor = motor_id
            for buf in self.buffers[motor_id].values():
                buf.clear()

    def update_scale(self, sig_id: str, ymin: float, ymax: float):
        """Updates scaling parameters for a specific signal."""
        if sig_id in self.scale:
            self.scale[sig_id] = (ymin, ymax)

    def store_frame(self, decoded_frame: dict):
        """
        Parses a decoded frame dictionary and appends values to the appropriate buffers.
        """
        motor = decoded_frame.get("motor")
        if motor not in self.buffers:
            return

        loop_cntr = decoded_frame["loopCntr"]
        motor_bufs = self.buffers[motor]

        # Store time base
        motor_bufs["__loop__"].append(loop_cntr)

        # Store signals based on mapping
        for sig_id, field in self.field_map.items():
            motor_bufs[sig_id].append(decoded_frame[field])

    def get_plot_data(self, sample_period_s: float) -> Optional[dict]:
        """
        Processes raw buffers into normalized Numpy arrays for the plotter.
        Returns None if there isn't enough data.
        """
        if self.selected_motor not in self.buffers:
            return None

        motor_bufs = self.buffers[self.selected_motor]
        loops_cntr = motor_bufs["__loop__"]

        if len(loops_cntr) < 2:
            return None

        # Build time axis
        loop_cntr_arr = np.asarray(loops_cntr, dtype=float)
        time_axis = loop_cntr_arr * sample_period_s

        snapshot_raw = {}
        out_norm = {}

        for sig_id, buf in motor_bufs.items():
            if sig_id == "__loop__":
                continue

            arr = np.asarray(buf, dtype=float)

            # Sanity check: ensure array lengths match time axis
            if len(arr) != len(time_axis):
                continue

            snapshot_raw[sig_id] = arr

            # Normalize data
            if sig_id in self.scale:
                ymin, ymax = self.scale[sig_id]
                scale = max(ymax - ymin, 1e-12)
                out_norm[sig_id] = np.clip((arr - ymin) / scale, 0.0, 1.0)

        return {
            "time": time_axis,
            "signals": out_norm,
            "raw": snapshot_raw,
        }
