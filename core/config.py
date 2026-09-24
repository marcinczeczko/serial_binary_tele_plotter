"""
Loading and validation of telemetry stream configurations (`streams.json`).

`validate_config()` is the single source of truth for what a usable stream definition is.
The loader uses it to keep broken streams out of the UI. The config editor uses it to
refuse saving a file that wouldn't load.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from core.protocol.constants import LOOP_CNTR_NAME, STRUCT_TYPE_MAP
from core.types import StreamConfig

PANEL_TYPES = ("none", "pid", "imu")
ENDIANNESS = ("little", "big")
MAX_PAYLOAD_BYTES = 255  # LEN is a single byte on the wire


@dataclass(frozen=True)
class ConfigProblem:
    severity: Literal["error", "warning"]
    stream: str | None
    message: str

    def __str__(self) -> str:
        where = f"[{self.stream}] " if self.stream else ""
        return f"{self.severity.upper()}: {where}{self.message}"


def validate_config(data: Any) -> list[ConfigProblem]:
    """Returns every problem found in a parsed `streams.json` document."""
    if not isinstance(data, dict) or not isinstance(data.get("streams"), dict):
        return [ConfigProblem("error", None, "missing or invalid 'streams' object")]
    problems: list[ConfigProblem] = []
    for key, stream in data["streams"].items():
        problems.extend(validate_stream(str(key), stream))
    return problems


def validate_stream(key: str, stream: Any) -> list[ConfigProblem]:
    """Returns the problems of a single stream definition."""
    problems: list[ConfigProblem] = []

    def error(msg: str) -> None:
        problems.append(ConfigProblem("error", key, msg))

    def warning(msg: str) -> None:
        problems.append(ConfigProblem("warning", key, msg))

    if not isinstance(stream, dict):
        error("stream entry must be an object")
        return problems
    if not isinstance(stream.get("name"), str) or not stream["name"]:
        error("'name' must be a non-empty string")
    panel_type = stream.get("panel_type", "none")
    if panel_type not in PANEL_TYPES:
        warning(f"unknown panel_type '{panel_type}' (known: {', '.join(PANEL_TYPES)})")

    frame = stream.get("frame")
    if not isinstance(frame, dict):
        error("'frame' must be an object")
        return problems

    stream_id = frame.get("stream_id")
    if not isinstance(stream_id, int) or isinstance(stream_id, bool) or not 0 <= stream_id <= 255:
        error(f"frame.stream_id must be an integer 0-255, got {stream_id!r}")
    endianness = frame.get("endianness", "little")
    if endianness not in ENDIANNESS:
        error(f"frame.endianness must be 'little' or 'big', got {endianness!r}")

    fields = frame.get("fields")
    if not isinstance(fields, list) or not fields:
        error("frame.fields must be a non-empty list")
        return problems

    names: list[str] = []
    fmt = ""
    for i, field in enumerate(fields):
        name = field.get("name") if isinstance(field, dict) else None
        ftype = field.get("type") if isinstance(field, dict) else None
        if not isinstance(name, str) or not name:
            error(f"frame.fields[{i}] needs a non-empty 'name'")
            continue
        if name in names:
            error(f"duplicate field name '{name}'")
        names.append(name)
        if ftype not in STRUCT_TYPE_MAP:
            error(
                f"field '{name}' has unknown type {ftype!r} (known: {', '.join(STRUCT_TYPE_MAP)})"
            )
        else:
            fmt += STRUCT_TYPE_MAP[ftype][0]

    payload = struct.calcsize("<" + fmt)
    if payload > MAX_PAYLOAD_BYTES:
        error(f"payload is {payload} B; the protocol allows at most {MAX_PAYLOAD_BYTES} B")

    if LOOP_CNTR_NAME not in names:
        error(f"frame must contain '{LOOP_CNTR_NAME}' (it drives the time axis)")
    else:
        if names[0] != LOOP_CNTR_NAME:
            warning(f"'{LOOP_CNTR_NAME}' is conventionally the first field")
        cntr = next(f for f in fields if isinstance(f, dict) and f.get("name") == LOOP_CNTR_NAME)
        if cntr.get("type") != "u32":
            warning(f"'{LOOP_CNTR_NAME}' should be u32, got {cntr.get('type')!r}")

    signals = stream.get("signals", {})
    if not isinstance(signals, dict):
        error("'signals' must be an object")
        return problems
    for sig_key, sig in signals.items():
        if not isinstance(sig, dict):
            error(f"signal '{sig_key}' must be an object")
            continue
        field_name = sig.get("field")
        if field_name not in names:
            error(f"signal '{sig_key}' maps to field {field_name!r}, which is not in the frame")
    return problems


class StreamConfigLoader:
    """
    Loads `streams.json` and exposes the streams that are valid.

    Streams with errors are left out of `list_streams()`; every problem (errors and
    warnings) is available in `problems` so the UI can report it.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._streams: dict[str, StreamConfig] = {}
        self.data: dict[str, Any] = {}
        self.problems: list[ConfigProblem] = []

        if not self.path.exists():
            raise FileNotFoundError(f"Required configuration file not found: {self.path.resolve()}")

        self.load()

    def load(self) -> None:
        """Loads, validates and filters the JSON configuration file."""
        try:
            with self.path.open("r", encoding="utf-8") as f:
                self.data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {self.path}: {e}") from e

        self.problems = validate_config(self.data)
        if any(p.stream is None for p in self.problems):
            raise ValueError(f"Invalid {self.path.name}: missing or invalid 'streams' section")

        broken = {p.stream for p in self.problems if p.severity == "error"}
        self._streams = {}
        for key, stream in self.data["streams"].items():
            if key in broken:
                continue
            stream.setdefault("panel_type", "none")
            self._streams[key] = stream

    def list_streams(self) -> dict[str, StreamConfig]:
        """Returns all valid stream definitions."""
        return self._streams

    def get_stream(self, stream_id: str) -> StreamConfig:
        """Returns configuration for a specific stream."""
        try:
            return self._streams[stream_id]
        except KeyError as e:
            raise KeyError(f"Stream '{stream_id}' not found in streams.json") from e
