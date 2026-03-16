"""
Shared Types Module.

Contains Enums and Type Aliases used across the entire application to prevent
circular imports.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum, auto
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    import numpy as np


class PlotMode(Enum):
    """Operational modes of the plotter visualization."""

    LIVE = 1
    ANALYSIS = 2


class EngineState(Enum):
    """Lifecycle states of the Telemetry Engine."""

    IDLE = auto()
    CONFIGURED = auto()
    RUNNING = auto()


class SignalLineConfig(TypedDict, total=False):
    style: str
    width: int


class StreamSignalConfig(TypedDict, total=False):
    label: str
    field: str
    color: str
    visible: bool
    line: SignalLineConfig


SignalsConfig = dict[str, StreamSignalConfig]


class StreamFrameField(TypedDict):
    name: str
    type: str


class StreamFrameConfig(TypedDict, total=False):
    stream_id: int
    endianness: str
    packed: bool
    fields: list[StreamFrameField]


class StreamConfig(TypedDict, total=False):
    name: str
    panel_type: str
    frame: StreamFrameConfig
    signals: SignalsConfig


DecodedFrame = Mapping[str, float | int]


class PlotPacket(TypedDict):
    time: np.ndarray
    signals: dict[str, np.ndarray]


class PlotPacketWithRaw(PlotPacket, total=False):
    raw: dict[str, np.ndarray]
    signal_bounds: dict[str, tuple[float, float]]  # pre-computed (min, max) per signal
