"""
Per-stream time base (R2.5, review finding C2).

A stream's X axis comes from a frame field (by default `loop_cntr`), and the stream's
config says how many seconds one tick of it is:

    "time": {"field": "loop_cntr", "scale_s": 0.005, "step": 1}

`TimeBase` turns the raw field values into *monotonic* ticks as frames are stored:
- **Wrap:** an integer field that rolls over (u32 after 2^32 ticks, u16 after 65536) is
  unwrapped, so time keeps growing.
- **Reset:** a jump backwards that isn't a wrap (the MCU restarted, so the counter began
  again) starts a new segment. It's placed right after the old one, so time never runs
  backwards, and it's separated by a gap.
- **Gap:** a forward jump of more than `GAP_FACTOR x step` (frames lost) gets a gap marker:
  a sample of NaNs that breaks the drawn line, so missing data is visible, not interpolated.

Seconds are applied only when the GUI reads a snapshot (`ticks x scale_s`). So correcting
the scale re-times the whole history consistently, and a reset or wrap can't leave the
cursor readout (which needs sorted time) looking at garbage.

Pure numpy, no Qt.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

import numpy as np

from core.protocol.constants import LOOP_CNTR_NAME, STRUCT_TYPE_MAP
from core.types import StreamConfig

DEFAULT_SCALE_S = 0.005  # the UI's historical default period (5 ms per loop_cntr tick)
GAP_FACTOR = 1.5  # a tick difference above GAP_FACTOR x step is a gap (lost frames)
# A backwards jump counts as a wrap only if the distance forward through the wrap is at
# most this many frames (and at most half the field's range). Anything else is a reset.
WRAP_WINDOW_FRAMES = 1000
TIME_KEYS = ("field", "scale_s", "step")


@dataclass(frozen=True)
class TimeBaseConfig:
    field: str = LOOP_CNTR_NAME
    scale_s: float = DEFAULT_SCALE_S
    """Seconds per tick of `field`."""
    step: float = 1.0
    """Nominal increment of `field` from one frame to the next (1 for a loop counter)."""
    modulus: float | None = 2.0**32
    """Where an integer field wraps (2**bits); None for float fields, which never wrap."""

    @property
    def period_s(self) -> float:
        """Nominal time between frames."""
        return self.scale_s * self.step


def field_modulus(ftype: str | None) -> float | None:
    """2**bits for integer field types, None for floats (and unknown types)."""
    if ftype not in STRUCT_TYPE_MAP:
        return None
    code = STRUCT_TYPE_MAP[ftype][0]
    if code in "fd":
        return None
    return 2.0 ** (8 * struct.calcsize("<" + code))


def time_base_config(stream: StreamConfig | dict[str, Any]) -> TimeBaseConfig:
    """
    Reads a stream's `time` block, filling in defaults.

    Assumes the stream passed `validate_stream` (which checks the block's types).
    """
    raw: Any = stream.get("time")
    time_cfg: dict[str, Any] = raw if isinstance(raw, dict) else {}
    field = str(time_cfg.get("field", LOOP_CNTR_NAME))
    frame = stream.get("frame")
    fields = frame.get("fields", []) if isinstance(frame, dict) else []
    ftype = next(
        (f.get("type") for f in fields if isinstance(f, dict) and f.get("name") == field),
        "u32",  # no frame (e.g. bare signal config): assume the conventional u32 counter
    )
    return TimeBaseConfig(
        field=field,
        scale_s=float(time_cfg.get("scale_s", DEFAULT_SCALE_S)),
        step=float(time_cfg.get("step", 1)),
        modulus=field_modulus(ftype),
    )


class TimeBase:
    """
    Stateful unwrapper for one stream: raw field values in, monotonic ticks out.

    Not thread-safe on its own; the owning `SampleStore` calls it under its lock.
    """

    def __init__(self, cfg: TimeBaseConfig | None = None) -> None:
        self.cfg = cfg or TimeBaseConfig()
        self.resets = 0
        """Segments started because the field went backwards (device reset)."""
        self.gaps = 0
        """Gap markers inserted for lost frames (resets excluded)."""
        self._last_raw: float | None = None
        self._last_tick = 0.0

    def reset(self) -> None:
        """Forgets the history (new acquisition session); counters restart at 0."""
        self.resets = 0
        self.gaps = 0
        self._last_raw = None
        self._last_tick = 0.0

    def process(self, raw: np.ndarray) -> tuple[np.ndarray, list[int], list[float]]:
        """
        Converts one batch of raw field values, in arrival order.

        Returns `(ticks, gap_index, gap_ticks)`: the monotonic tick of every sample, and
        the gap markers to insert, as indices into the batch (a marker goes *before* that
        sample) with the marker's tick.
        """
        # A plain loop, like the router's counter tracking: batches are a read's worth of
        # frames, where the fixed cost of ~20 numpy calls would dominate (measured: it
        # halved parse+store throughput at 900 B reads).
        cfg = self.cfg
        step = cfg.step
        gap_limit = GAP_FACTOR * step
        modulus = cfg.modulus
        window = min(WRAP_WINDOW_FRAMES * step, modulus / 2) if modulus is not None else 0.0
        last_raw, tick = self._last_raw, self._last_tick
        ticks: list[float] = []
        gap_index: list[int] = []
        gap_ticks: list[float] = []
        for i, value in enumerate(raw.tolist()):
            if value != value:  # NaN: a missing value, assume the nominal step
                tick += step
                ticks.append(tick)
                continue
            if last_raw is None:
                tick = value  # the first sample keeps its own value as the origin
            else:
                d = value - last_raw
                if d < 0 and modulus is not None and d + modulus <= window:
                    d += modulus  # wrapped
                if d < 0:  # went backwards: a new segment, after a gap marker
                    self.resets += 1
                    gap_index.append(i)
                    gap_ticks.append(tick + step)
                    d = 2 * step
                elif d > gap_limit:  # frames lost
                    self.gaps += 1
                    gap_index.append(i)
                    gap_ticks.append(tick + step)
                tick += d
            last_raw = value
            ticks.append(tick)
        self._last_raw, self._last_tick = last_raw, tick
        return np.asarray(ticks, dtype=np.float64), gap_index, gap_ticks
