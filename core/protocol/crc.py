"""
CRC Utility Module.
"""


def _build_crc8_table() -> list[int]:
    """Pre-compute the 256-entry lookup table once at module import time."""
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
        table.append(crc)
    return table


# Module-level constant: built once, reused for every CRC call
_CRC8_TABLE: list[int] = _build_crc8_table()


def calculate_crc8(data: bytes) -> int:
    """
    Computes the 8-bit Cyclic Redundancy Check (CRC) for the given data.
    Polynomial: 0x07 (x^8 + x^2 + x + 1).
    Uses a 256-entry lookup table: O(1) per byte instead of 8 iterations.
    """
    crc = 0x00
    for byte in data:
        crc = _CRC8_TABLE[crc ^ byte]  # single table lookup replaces 8 bit-shifts
    return crc
