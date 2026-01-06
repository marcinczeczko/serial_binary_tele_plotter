"""
Protocol Constants.

Defines magic numbers, Packet IDs, and data type mappings used
for binary frame parsing.
"""

# Frame Synchronization Markers
MAGIC_0 = 0xAA
MAGIC_1 = 0x55

# Packet IDs
RTP_REQ_PID = 0x10  # Configuration Request

# Mapping: JSON type string -> (struct format char, size in bytes)
STRUCT_TYPE_MAP = {
    "u8": ("B", 1, "uint8"),
    "i8": ("b", 1, "int8"),
    "u16": ("H", 2, "uint16"),
    "i16": ("h", 2, "int16"),
    "u32": ("I", 4, "uint32"),
    "i32": ("i", 4, "int32"),
    "f32": ("f", 4, "float32"),
}

LOOP_CNTR_NAME = "loop_cntr"
