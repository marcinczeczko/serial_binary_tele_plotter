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


class YRangeConfig(TypedDict, total=False):
    mode: str  # auto | auto-grow | manual
    min: float
    max: float
    include_zero: bool


class StreamSignalConfig(TypedDict, total=False):
    label: str
    field: str
    color: str
    visible: bool
    line: SignalLineConfig
    group: str  # the lane the signal is drawn in
    y_range: YRangeConfig


class GroupConfig(TypedDict, total=False):
    label: str
    order: float
    y_range: YRangeConfig


SignalsConfig = dict[str, StreamSignalConfig]


class StreamFrameField(TypedDict):
    name: str
    type: str


class StreamFrameConfig(TypedDict, total=False):
    stream_id: int
    endianness: str
    packed: bool
    fields: list[StreamFrameField]


class StreamTimeConfig(TypedDict, total=False):
    field: str
    scale_s: float
    step: float


class StreamSimConfig(TypedDict, total=False):
    model: str
    fields: dict[str, dict[str, float | str]]


class StreamConfig(TypedDict, total=False):
    name: str
    controls: str  # the key of a panel in the document's `panels` (R5.2)
    frame: StreamFrameConfig
    time: StreamTimeConfig
    sim: StreamSimConfig
    groups: dict[str, GroupConfig]
    signals: SignalsConfig


DecodedFrame = Mapping[str, float | int]


class PlotPacket(TypedDict):
    time: np.ndarray
    signals: dict[str, np.ndarray]


class PlotPacketWithBounds(PlotPacket, total=False):
    signal_bounds: dict[str, tuple[float, float]]  # pre-computed (min, max) per signal
