"""
Core protocol definitions and utility functions.

This module contains the communication protocol constants (Magic Bytes, Packet IDs),
application state enumerations, and data integrity calculation functions (CRC8)
used throughout the telemetry application.
"""

from enum import Enum

# Protocol constants
MAGIC_0 = 0xAA
MAGIC_1 = 0x55
RTP_PID = 0x01
RTP_REQ_PID = 0x10


class PlotMode(Enum):
    """
    Enumeration representing the operational modes of the plotter.

    Attributes:
        LIVE (1): The plotter updates in real-time with incoming data.
        ANALYSIS (2): The plotter is paused, allowing inspection of historical data.
    """

    LIVE = 1
    ANALYSIS = 2


def calculate_crc8(data: bytes) -> int:
    """
    Computes the 8-bit Cyclic Redundancy Check (CRC) for the given data.

    This implementation uses the standard polynomial 0x07 (x^8 + x^2 + x + 1),
    commonly used in embedded systems communication.

    Args:
        data (bytes): The sequence of bytes to calculate the CRC for.

    Returns:
        int: The calculated 8-bit CRC checksum.
    """
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc
