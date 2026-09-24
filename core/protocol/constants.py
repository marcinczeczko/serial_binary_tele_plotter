"""
Protocol Constants.

Defines the sync markers and the data type mappings used
for binary frame parsing.
"""

# Frame Synchronization Markers
MAGIC_0 = 0xAA
MAGIC_1 = 0x55

# Host -> MCU command packets (IDs and layouts) are defined in streams.json `commands`
# (R5.2, `core.protocol.commands`), not here.


# Mapping: JSON type string -> (struct format char, size in bytes)
STRUCT_TYPE_MAP = {
    "u8": ("B", 1, "uint8"),
    "i8": ("b", 1, "int8"),
    "u16": ("H", 2, "uint16"),
    "i16": ("h", 2, "int16"),
    "u32": ("I", 4, "uint32"),
    "i32": ("i", 4, "int32"),
    "f32": ("f", 4, "float32"),
    "u64": ("Q", 8, "uint64"),
    "i64": ("q", 8, "int64"),
    "f64": ("d", 8, "float64"),
}

LOOP_CNTR_NAME = "loop_cntr"
