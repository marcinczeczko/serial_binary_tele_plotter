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
from typing import Any, Literal, cast

from core.acquisition.timebase import TIME_KEYS
from core.protocol.constants import LOOP_CNTR_NAME, STRUCT_TYPE_MAP
from core.simulation.synth import SIM_KEYS, SIM_MODELS, WAVE_KEYS, WAVES
from core.types import StreamConfig

# The streams.json shipped next to the application code (not the current working directory).
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "streams.json"

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


def resolve_config_path(
    cli_path: str | Path | None,
    remembered_path: str | Path | None = None,
    default: Path = DEFAULT_CONFIG_PATH,
) -> Path:
    """
    Picks the configuration file (C10): an explicit `--config` wins (even if missing, so
    the user gets an error about the file they asked for). Otherwise the last used file,
    if it still exists. Otherwise the bundled default. Never the current working directory.
    """
    if cli_path:
        return Path(cli_path).expanduser().resolve()
    if remembered_path and Path(remembered_path).is_file():
        return Path(remembered_path).resolve()
    return default


def validate_config(data: Any) -> list[ConfigProblem]:
    """Returns every problem found in a parsed `streams.json` document."""
    if not isinstance(data, dict) or not isinstance(data.get("streams"), dict):
        return [ConfigProblem("error", None, "missing or invalid 'streams' object")]
    problems: list[ConfigProblem] = []
    for key, stream in data["streams"].items():
        problems.extend(validate_stream(str(key), stream))
    problems.extend(_shared_id_problems(data["streams"]))
    return problems


def _shared_id_problems(streams: dict[str, Any]) -> list[ConfigProblem]:
    """
    Streams may share a stream_id. The router tells them apart by payload size and decodes
    identical layouts once. Two *different* layouts of the same size are ambiguous: only
    the first is decoded.
    """
    problems: list[ConfigProblem] = []
    seen: dict[tuple[int, int], tuple[str, tuple[Any, ...]]] = {}
    for key, stream in streams.items():
        try:
            frame = stream["frame"]
            fields = frame["fields"]
            layout = (frame.get("endianness", "little"),) + tuple(f["type"] for f in fields)
            size = struct.calcsize("<" + "".join(STRUCT_TYPE_MAP[t][0] for t in layout[1:]))
            slot = (int(frame["stream_id"]), size)
        except KeyError, TypeError, ValueError:
            continue  # already reported by validate_stream
        if slot not in seen:
            seen[slot] = (str(key), layout)
        elif seen[slot][1] != layout:
            first = seen[slot][0]
            problems.append(
                ConfigProblem(
                    "warning",
                    str(key),
                    f"shares stream_id {slot[0]} and a {size} B payload with '{first}' but has "
                    f"a different layout; frames can't be told apart, only '{first}' is decoded",
                )
            )
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
        error(f"frame must contain '{LOOP_CNTR_NAME}' (it's used to detect lost frames)")
    else:
        if names[0] != LOOP_CNTR_NAME:
            warning(f"'{LOOP_CNTR_NAME}' is conventionally the first field")
        cntr = next(f for f in fields if isinstance(f, dict) and f.get("name") == LOOP_CNTR_NAME)
        if cntr.get("type") != "u32":
            warning(f"'{LOOP_CNTR_NAME}' should be u32, got {cntr.get('type')!r}")

    problems.extend(_time_problems(key, stream.get("time"), names))
    problems.extend(_sim_problems(key, stream.get("sim"), names))

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


def _is_positive_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and value > 0


def _time_problems(key: str, time_cfg: Any, names: list[str]) -> list[ConfigProblem]:
    """Checks the optional `time: {field, scale_s, step}` block (R2.5)."""
    if time_cfg is None:
        return []
    if not isinstance(time_cfg, dict):
        return [ConfigProblem("error", key, "'time' must be an object")]
    problems: list[ConfigProblem] = []
    field = time_cfg.get("field", LOOP_CNTR_NAME)
    if field not in names:
        problems.append(
            ConfigProblem("error", key, f"time.field {field!r} is not a field of the frame")
        )
    for name in ("scale_s", "step"):
        if name in time_cfg and not _is_positive_number(time_cfg[name]):
            problems.append(
                ConfigProblem(
                    "error", key, f"time.{name} must be a positive number, got {time_cfg[name]!r}"
                )
            )
    if field != LOOP_CNTR_NAME and "step" not in time_cfg:
        problems.append(
            ConfigProblem(
                "warning",
                key,
                f"time.field is '{field}' but time.step is not set; gaps are detected against "
                "a step of 1 (set it to the field's increase per frame, e.g. 5000 for a "
                "microsecond timestamp at 200 Hz)",
            )
        )
    unknown = sorted(set(time_cfg) - set(TIME_KEYS))
    if unknown:
        problems.append(
            ConfigProblem(
                "warning",
                key,
                f"unknown time key(s) {', '.join(unknown)} (known: {', '.join(TIME_KEYS)})",
            )
        )
    return problems


def _sim_problems(key: str, sim: Any, names: list[str]) -> list[ConfigProblem]:
    """
    Checks the optional `sim` block (R2.7). It only affects the VIRTUAL port, so problems
    are warnings: a stream is never excluded because of its simulation settings.
    """
    if sim is None:
        return []

    def warning(msg: str) -> ConfigProblem:
        return ConfigProblem("warning", key, f"sim: {msg}")

    if not isinstance(sim, dict):
        return [warning("must be an object; ignored")]
    problems: list[ConfigProblem] = []
    unknown = sorted(set(sim) - set(SIM_KEYS))
    if unknown:
        problems.append(warning(f"unknown key(s) {', '.join(unknown)}"))
    model = sim.get("model")
    if model is not None and model not in SIM_MODELS:
        problems.append(warning(f"unknown model {model!r} (known: {', '.join(SIM_MODELS)})"))
    fields = sim.get("fields", {})
    if not isinstance(fields, dict):
        return [*problems, warning("'fields' must be an object; ignored")]
    for name, spec in fields.items():
        if name not in names:
            problems.append(warning(f"field {name!r} is not in the frame"))
        if not isinstance(spec, dict):
            problems.append(warning(f"fields.{name} must be an object"))
            continue
        if spec.get("wave", "sine") not in WAVES:
            problems.append(
                warning(f"fields.{name}: unknown wave {spec['wave']!r} (known: {', '.join(WAVES)})")
            )
        for param, value in spec.items():
            if param not in WAVE_KEYS:
                problems.append(warning(f"fields.{name}: unknown key {param!r}"))
            elif param != "wave" and (
                not isinstance(value, int | float) or isinstance(value, bool)
            ):
                problems.append(warning(f"fields.{name}.{param} must be a number"))
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
            # Copy with defaults applied: `self.data` stays exactly as read, because the
            # config editor round-trips it.
            entry = {**stream, "panel_type": stream.get("panel_type", "none")}
            self._streams[key] = cast(StreamConfig, entry)

    def list_streams(self) -> dict[str, StreamConfig]:
        """Returns all valid stream definitions."""
        return self._streams

    def get_stream(self, stream_id: str) -> StreamConfig:
        """Returns configuration for a specific stream."""
        try:
            return self._streams[stream_id]
        except KeyError as e:
            raise KeyError(f"Stream '{stream_id}' not found in streams.json") from e
