"""
CRC Utility Module.
"""


def calculate_crc8(data: bytes) -> int:
    """
    Computes the 8-bit Cyclic Redundancy Check (CRC) for the given data.
    Polynomial: 0x07 (x^8 + x^2 + x + 1).
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
