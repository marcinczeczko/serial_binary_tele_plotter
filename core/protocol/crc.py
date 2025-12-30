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
