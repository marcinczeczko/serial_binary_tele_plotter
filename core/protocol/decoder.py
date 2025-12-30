"""
Frame Decoder Module.

This module provides the `FrameDecoder` class, which is responsible for converting
raw binary payloads (bytes) into structured Python dictionaries. It acts as a wrapper
around Python's built-in `struct` module, allowing the binary structure to be defined
dynamically via configuration files rather than hardcoded classes.
"""

import struct

from core.protocol.constants import STRUCT_TYPE_MAP


class FrameDecoder:
    """
    Dynamically decodes binary payloads based on a field definition list.

    Instead of hardcoding binary structures (like C structs), this class builds
    the `struct` format string at runtime based on the list of fields provided
    in the configuration (streams.json).

    Attributes:
        fields (list): List of field definitions (dictionaries containing name and type).
        endian (str): Struct endianness character ('<' for little, '>' for big).
        format (str): The compiled struct format string (e.g., "<IfBf").
        names (list): Pre-cached list of field names to map unpacked values quickly.
    """

    def __init__(self, endian: str, fields: list):
        """
        Initializes the FrameDecoder.

        Args:
            endian (str): Data endianness ("little" or "big").
            fields (list): A list of field dicts, e.g.:
                           [{'name': 'cnt', 'type': 'u32'}, {'name': 'val', 'type': 'f32'}]
        """
        self.fields = fields

        # Translate readable endianness to struct format characters
        # '<' = Little-endian (standard for ARM Cortex-M)
        # '>' = Big-endian
        self.endian = "<" if endian == "little" else ">"

        # Pre-compile the format string for performance
        self.format = self._build_struct_fmt()

        # Cache names to avoid re-iterating the list during high-frequency decoding
        self.names = [f["name"] for f in fields]

    def _build_struct_fmt(self) -> str:
        """
        Constructs the Python struct format string from field definitions.

        Iterates through the fields config, looks up the corresponding struct char
        in `STRUCT_TYPE_MAP` (e.g., 'u32' -> 'I'), and appends it to the string.

        Returns:
            str: The complete format string (e.g., "<IIffB").
        """
        fmt = self.endian
        for f in self.fields:
            # Look up the struct code (e.g., 'I') for the given type name (e.g., 'u32')
            if f["type"] not in STRUCT_TYPE_MAP:
                raise ValueError(f"Unknown type '{f['type']}' in field definition.")

            code, _ = STRUCT_TYPE_MAP[f["type"]]
            fmt += code
        return fmt

    def decode(self, payload: bytes) -> dict:
        """
        Unpacks binary data into a dictionary.

        Args:
            payload (bytes): The raw byte sequence to decode. Must match self.size.

        Returns:
            dict: Key-value pairs where keys are field names and values are unpacked numbers.

        Raises:
            struct.error: If the payload length does not match the format size.
        """
        # struct.unpack returns a tuple of values
        values = struct.unpack(self.format, payload)

        # Zip combines the names list with the values tuple to create the dict
        # Example: zip(['cnt', 'val'], (100, 3.14)) -> {'cnt': 100, 'val': 3.14}
        return dict(zip(self.names, values))

    @property
    def size(self) -> int:
        """
        Returns the expected size of the binary frame in bytes.

        This is useful for the ProtocolHandler to know how many bytes to wait for
        before attempting to decode a payload.
        """
        return struct.calcsize(self.format)
