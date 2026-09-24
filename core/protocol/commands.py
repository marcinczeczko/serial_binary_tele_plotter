"""
Host → MCU command packets defined in config (R5.2). Pure: no Qt, no config parsing.

A command is a packet ID and a packed struct of `fields`, framed like telemetry:
`[AA 55][packet_id][LEN][H_CRC][payload][P_CRC]`. The payload layout reuses the telemetry
frame dtype (`frame_dtype`), so a command and a frame with the same fields pack the same
bytes. What goes into each field is decided when a command is sent:

- a button's own `values` (e.g. `motor_id: 0` on "Update Left"),
- else the field's constant `value`,
- else the panel parameter named by `param`, from the field's `column`, or else from the
  button's column.

`encode_command` refuses a value that doesn't fit its field (e.g. 300 in a `u8`), rather
than letting numpy wrap it: a silently wrapped gain would be sent to real hardware.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from core.protocol.constants import MAGIC_0, MAGIC_1, STRUCT_TYPE_MAP
from core.protocol.crc import calculate_crc8
from core.protocol.record_decoder import frame_dtype
from core.types import StreamFrameField

MAX_PAYLOAD_BYTES = 255  # LEN is one byte

CommandValue = float | int | bool


class CommandError(ValueError):
    """A command can't be encoded with the given values."""


@dataclass(frozen=True)
class CommandField:
    name: str
    type: str
    value: float | None = None  # a constant
    param: str | None = None  # a panel parameter
    column: str | None = None  # the parameter's column (else the button's)


@dataclass(frozen=True)
class CommandDef:
    key: str
    label: str
    packet_id: int
    fields: tuple[CommandField, ...]
    endianness: str = "little"

    @property
    def layout(self) -> list[StreamFrameField]:
        return [{"name": f.name, "type": f.type} for f in self.fields]

    @property
    def payload_size(self) -> int:
        return int(frame_dtype(self.endianness, self.layout).itemsize)


def resolve_values(
    command: CommandDef,
    params: Mapping[str, Mapping[str, CommandValue]],
    column: str | None = None,
    fixed: Mapping[str, CommandValue] | None = None,
) -> dict[str, CommandValue]:
    """
    Every field's value for one send. `params` is {column: {parameter: value}}; `column`
    is the pressing button's column and `fixed` its own values.
    """
    fixed = fixed or {}
    out: dict[str, CommandValue] = {}
    for f in command.fields:
        if f.name in fixed:
            out[f.name] = fixed[f.name]
        elif f.value is not None:
            out[f.name] = f.value
        elif f.param is not None:
            col = f.column if f.column is not None else column
            if col is None or col not in params or f.param not in params[col]:
                raise CommandError(
                    f"{command.key}.{f.name}: parameter '{f.param}' has no value"
                    + (f" in column '{col}'" if col is not None else " (no column)")
                )
            out[f.name] = params[col][f.param]
        else:
            raise CommandError(f"{command.key}.{f.name}: no value given")
    return out


def encode_payload(command: CommandDef, values: Mapping[str, CommandValue]) -> bytes:
    dtype = frame_dtype(command.endianness, command.layout)
    record = np.zeros(1, dtype=dtype)
    for f in command.fields:
        if f.name not in values:
            raise CommandError(f"{command.key}.{f.name}: no value given")
        record[f.name] = _checked(command.key, f, values[f.name])
    return record.tobytes()


def encode_command(command: CommandDef, values: Mapping[str, CommandValue]) -> bytes:
    """The complete framed packet (header, CRCs) for one send."""
    payload = encode_payload(command, values)
    header = bytes([MAGIC_0, MAGIC_1, command.packet_id, len(payload)])
    return header + bytes([calculate_crc8(header)]) + payload + bytes([calculate_crc8(payload)])


def decode_command(command: CommandDef, payload: bytes) -> dict[str, CommandValue] | None:
    """A received payload's field values, as the firmware would read them (None: wrong size)."""
    dtype = frame_dtype(command.endianness, command.layout)
    if len(payload) != dtype.itemsize:
        return None
    record = np.frombuffer(payload, dtype=dtype)[0]
    return {f.name: record[f.name].item() for f in command.fields}


def _checked(key: str, f: CommandField, value: CommandValue) -> CommandValue:
    kind = np.dtype(STRUCT_TYPE_MAP[f.type][2])
    number = float(value)
    if kind.kind == "f":
        if kind.itemsize == 4 and math.isfinite(number) and abs(number) > float(np.finfo(kind).max):
            raise CommandError(f"{key}.{f.name}: {value} doesn't fit in {f.type}")
        return number
    if not math.isfinite(number) or number != int(number):
        raise CommandError(f"{key}.{f.name}: {f.type} needs a whole number, got {value}")
    info = np.iinfo(kind)
    if not info.min <= int(number) <= info.max:
        raise CommandError(
            f"{key}.{f.name}: {int(number)} is outside {f.type} ({info.min}..{info.max})"
        )
    return int(number)
