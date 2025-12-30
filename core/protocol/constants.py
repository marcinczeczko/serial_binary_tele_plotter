"""
Protocol Constants.

Defines magic numbers, Packet IDs, and data type mappings used
for binary frame parsing.
"""

# Frame Synchronization Markers
MAGIC_0 = 0xAA
MAGIC_1 = 0x55

# Packet IDs
RTP_PID = 0x01  # Real-time Telemetry Data
RTP_REQ_PID = 0x10  # Configuration Request

# Mapping: JSON type string -> (struct format char, size in bytes)
STRUCT_TYPE_MAP = {
    "u8": ("B", 1),
    "i8": ("b", 1),
    "u16": ("H", 2),
    "i16": ("h", 2),
    "u32": ("I", 4),
    "i32": ("i", 4),
    "f32": ("f", 4),
}
