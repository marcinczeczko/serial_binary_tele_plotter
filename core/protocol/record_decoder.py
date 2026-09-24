"""
Vectorised payload decoding (R2.3): a numpy structured dtype per frame layout.

A batch of same-layout payloads is joined and decoded with one `np.frombuffer` call into
a structured array (one record per frame, one column per field). This replaces
`struct.unpack` plus a dict per frame.
"""

from __future__ import annotations

import numpy as np

from core.protocol.constants import STRUCT_TYPE_MAP
from core.types import StreamFrameField


def frame_dtype(endianness: str, fields: list[StreamFrameField]) -> np.dtype:
    """Packed (unaligned) structured dtype matching the MCU's packed struct."""
    order = "<" if endianness == "little" else ">"
    spec: list[tuple[str, str]] = []
    for f in fields:
        if f["type"] not in STRUCT_TYPE_MAP:
            raise ValueError(f"Unknown type '{f['type']}' in field definition.")
        spec.append((f["name"], order + np.dtype(STRUCT_TYPE_MAP[f["type"]][2]).str[1:]))
    return np.dtype(spec)


class RecordDecoder:
    def __init__(self, endianness: str, fields: list[StreamFrameField]) -> None:
        self.dtype = frame_dtype(endianness, fields)

    @property
    def size(self) -> int:
        return int(self.dtype.itemsize)

    def decode_many(self, payloads: list[bytes]) -> np.ndarray:
        """Decodes payloads that are all exactly `size` bytes into a structured array."""
        return np.frombuffer(b"".join(payloads), dtype=self.dtype)
