"""
Core protocol definitions and utility functions.

This module contains the communication protocol constants (Magic Bytes, Packet IDs),
application state enumerations, and data integrity calculation functions (CRC8)
used throughout the telemetry application.
"""

from enum import Enum, auto


class PlotMode(Enum):
    """
    Enumeration representing the operational modes of the plotter.

    Attributes:
        LIVE (1): The plotter updates in real-time with incoming data.
        ANALYSIS (2): The plotter is paused, allowing inspection of historical data.
    """

    LIVE = 1
    ANALYSIS = 2


class EngineState(Enum):
    """
    Represents the operational lifecycle states of the TelemetryEngine.

    This state machine governs the internal logic to ensure data integrity
    and prevent invalid operations, such as attempting to acquire data
    before the signal mapping configuration is fully loaded.

    Attributes:
        IDLE: Initial state. No stream configuration or signal mappings are loaded.
        CONFIGURED: Signal definitions and frame decoders are applied. Ready to connect.
        RUNNING: Active data acquisition loop (Serial or Virtual) is executing.
    """

    IDLE = auto()
    CONFIGURED = auto()
    RUNNING = auto()
