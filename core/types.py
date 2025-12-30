"""
Shared Types Module.

Contains Enums and Type Aliases used across the entire application to prevent
circular imports.
"""

from enum import Enum, auto


class PlotMode(Enum):
    """Operational modes of the plotter visualization."""

    LIVE = 1
    ANALYSIS = 2


class EngineState(Enum):
    """Lifecycle states of the Telemetry Engine."""

    IDLE = auto()
    CONFIGURED = auto()
    RUNNING = auto()
