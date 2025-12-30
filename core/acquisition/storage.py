"""
Data Manager Module.

This module acts as the "Model" in the MVC-like architecture of the Worker.
It handles the storage, lifecycle, and transformation of telemetry data.
It isolates raw data buffering and numpy operations from the communication logic.
"""

from collections import deque
from typing import Deque, Dict, Optional, Union

import numpy as np

# Type alias for buffer elements (can be raw float or int counters)
BufferValue = Union[float, int]


class SignalDataManager:
    """
    Manages telemetry data buffers, scaling configurations, and signal mappings.

    This class encapsulates the state of the data being plotted. It handles:
    1. Storing raw data in efficient ring buffers (collections.deque).
    2. Mapping generic signal names (e.g., 'pTerm') to binary frame fields.
    3. Transforming raw data into normalized arrays (0.0 - 1.0) for the GUI.

    Attributes:
        buffers (Dict): Nested dictionary storing data: buffers[motor_id][signal_id] -> deque.
        scale (Dict): Stores Y-axis ranges: scale[signal_id] -> (min, max).
        field_map (Dict): Maps signal IDs to decoded frame keys.
    """

    def __init__(self, max_samples: int):
        """
        Initializes the Data Manager.

        Args:
            max_samples (int): The maximum size of the ring buffers (history length).
        """
        self.max_samples = max_samples
        self.selected_motor = 0

        # Structure: { motor_id: { 'signal_name': deque([values...]) } }
        self.buffers: Dict[int, Dict[str, Deque[BufferValue]]] = {}

        # Structure: { 'signal_name': (min_val, max_val) }
        self.scale: Dict[str, tuple] = {}

        # Structure: { 'signal_name': 'frame_field_name' }
        self.field_map: Dict[str, str] = {}

    def configure(self, signals_cfg: dict):
        """
        Initializes buffers and mappings based on the loaded configuration.

        Args:
            signals_cfg (dict): Configuration dict containing signal definitions.
        """
        self.buffers.clear()
        self.scale.clear()
        self.field_map.clear()

        # Load signal configurations
        for sig_id, sig in signals_cfg.items():
            self.field_map[sig_id] = sig["field"]
            yr = sig["y_range"]
            self.scale[sig_id] = (yr["min"], yr["max"])

        # Prepare buffers for supported motors.
        # Currently hardcoded for Motor 0 (Left) and Motor 1 (Right).
        for motor_id in (0, 1):
            motor_bufs = {"__loop__": deque(maxlen=self.max_samples)}

            for sig_id in signals_cfg.keys():
                motor_bufs[sig_id] = deque(maxlen=self.max_samples)

            self.buffers[motor_id] = motor_bufs

    def update_max_samples(self, max_samples: int):
        """
        Resizes all existing buffers to the new maximum sample count.
        Note: This involves creating new deques, as 'maxlen' is read-only.
        """
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
        """
        Sets the active motor for visualization.

        Buffers are cleared upon switching to prevent 'ghosting' (plotting
        old data from the previous motor mixed with new data).
        """
        if motor_id in self.buffers:
            self.selected_motor = motor_id
            for buf in self.buffers[motor_id].values():
                buf.clear()

    def update_scale(self, sig_id: str, ymin: float, ymax: float):
        """Updates scaling parameters (Y-min, Y-max) for a specific signal."""
        if sig_id in self.scale:
            self.scale[sig_id] = (ymin, ymax)

    def store_frame(self, decoded_frame: dict):
        """
        Ingests a decoded data frame and appends values to the appropriate buffers.

        Args:
            decoded_frame (dict): Dictionary output from FrameDecoder.
        """
        motor = decoded_frame.get("motor")

        # Drop frames for motors that are not configured
        if motor not in self.buffers:
            return

        loop_cntr = decoded_frame["loopCntr"]
        motor_bufs = self.buffers[motor]

        # Store time base (loop counter)
        motor_bufs["__loop__"].append(loop_cntr)

        # Store signals based on the configuration mapping
        for sig_id, field in self.field_map.items():
            motor_bufs[sig_id].append(decoded_frame[field])

    def get_plot_data(self, sample_period_s: float) -> Optional[dict]:
        """
        Processes raw buffers into normalized Numpy arrays for the GUI plotter.

        This method performs the heavy mathematical lifting:
        1. Converts Python lists (deques) to Numpy arrays.
        2. Normalizes values to the 0.0 - 1.0 range required by PyQtGraph ViewBoxes.

        Args:
            sample_period_s (float): Time duration of one sample (e.g. 0.005s).

        Returns:
            dict or None: A dictionary containing 'time', 'signals' (normalized),
                          and 'raw' (original values), or None if insufficient data.
        """
        if self.selected_motor not in self.buffers:
            return None

        motor_bufs = self.buffers[self.selected_motor]
        loops_cntr = motor_bufs["__loop__"]

        # Need at least 2 points to draw a line
        if len(loops_cntr) < 2:
            return None

        # Build time axis: time = sample_index * period
        # Using numpy here is much faster than list comprehension for large buffers
        loop_cntr_arr = np.asarray(loops_cntr, dtype=float)
        time_axis = loop_cntr_arr * sample_period_s

        snapshot_raw = {}
        out_norm = {}

        for sig_id, buf in motor_bufs.items():
            if sig_id == "__loop__":
                continue

            arr = np.asarray(buf, dtype=float)

            # Sanity check: ensure signal length matches time axis length
            # (In rare race conditions, one might be appended before the other)
            if len(arr) != len(time_axis):
                continue

            snapshot_raw[sig_id] = arr

            # Normalize data for visualization
            if sig_id in self.scale:
                ymin, ymax = self.scale[sig_id]

                # Prevent division by zero if min equals max
                scale = max(ymax - ymin, 1e-12)

                # Formula: normalized = (value - min) / (max - min)
                # Clip ensures data stays within 0.0-1.0 even if it slightly exceeds range
                out_norm[sig_id] = np.clip((arr - ymin) / scale, 0.0, 1.0)

        return {
            "time": time_axis,
            "signals": out_norm,
            "raw": snapshot_raw,
        }
