import struct

from core.frame_types import STRUCT_TYPE_MAP


class FrameDecoder:
    def __init__(self, endian: str, fields: list):
        self.fields = fields
        self.endian = "<" if endian == "little" else ">"
        self.format = self._build_struct_fmt()
        self.names = [f["name"] for f in fields]

    def _build_struct_fmt(self):
        fmt = self.endian
        for f in self.fields:
            code, _ = STRUCT_TYPE_MAP[f["type"]]
            fmt += code
        return fmt

    def decode(self, payload: bytes) -> dict:
        values = struct.unpack(self.format, payload)
        return dict(zip(self.names, values))

    @property
    def size(self) -> int:
        return struct.calcsize(self.format)
