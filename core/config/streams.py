"""
Validation of the stream definitions in `streams.json` (`streams.<key>`).

`validate_stream()` is the single source of truth for what a usable stream is: frame
layout (binary frames, or a text line pattern in a text profile, R8.3), time base,
simulation, lanes and signals. The document-level checks (schema
version, commands, panels, cross-references) are in `core.config.document`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Literal

from core.acquisition.timebase import TIME_KEYS
from core.protocol.constants import LOOP_CNTR_NAME, STRUCT_TYPE_MAP
from core.protocol.text_line import LINE_FIELD, PatternError, default_time_field, parse_pattern
from core.simulation.synth import SIM_KEYS, SIM_MODELS, WAVE_KEYS, WAVES

ENDIANNESS = ("little", "big")
MAX_PAYLOAD_BYTES = 255  # LEN is a single byte on the wire
Y_RANGE_MODES = ("auto", "auto-grow", "manual")  # per-lane Y range behaviour (R3.2)
GROUP_KEYS = ("label", "order", "y_range")
Y_RANGE_KEYS = ("mode", "min", "max", "include_zero")
BINARY_FRAME_KEYS = ("stream_id", "endianness", "packed")  # meaningless for text lines


@dataclass(frozen=True)
class ConfigProblem:
    """
    One problem in the document. `stream` names the stream it excludes or concerns;
    `item` names a command or panel instead (e.g. "command 'pid_single'"). A problem with
    neither concerns the whole document: an error there means the file can't be used.
    """

    severity: Literal["error", "warning"]
    stream: str | None
    message: str
    item: str | None = None

    @property
    def fatal(self) -> bool:
        return self.severity == "error" and self.stream is None and self.item is None

    def __str__(self) -> str:
        where = self.stream or self.item
        return f"{self.severity.upper()}: {f'[{where}] ' if where else ''}{self.message}"


def shared_id_problems(streams: dict[str, Any]) -> list[ConfigProblem]:
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


def same_pattern_problems(streams: dict[str, Any]) -> list[ConfigProblem]:
    """Text streams: the first pattern that matches a line wins, so a repeat never matches."""
    problems: list[ConfigProblem] = []
    seen: dict[str, str] = {}
    for key, stream in streams.items():
        frame = stream.get("frame") if isinstance(stream, dict) else None
        pattern = frame.get("pattern") if isinstance(frame, dict) else None
        if not isinstance(pattern, str):
            continue
        if pattern in seen:
            problems.append(
                ConfigProblem(
                    "warning",
                    str(key),
                    f"has the same pattern as '{seen[pattern]}', which gets every such line",
                )
            )
        else:
            seen[pattern] = str(key)
    return problems


def validate_stream(key: str, stream: Any, fmt: str = "binary") -> list[ConfigProblem]:
    """
    Returns the problems of a single stream definition, in a profile of format `fmt`
    (`binary` frames or `text` lines).
    """
    text = fmt == "text"
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
    if "controls" in stream and not isinstance(stream["controls"], str):
        warning("'controls' must be the key of a panel")

    frame = stream.get("frame")
    if not isinstance(frame, dict):
        error("'frame' must be an object")
        return problems

    if text:
        ignored = [k for k in BINARY_FRAME_KEYS if k in frame]
        if ignored:
            warning(f"frame.{', frame.'.join(ignored)} mean nothing for text lines; ignored")
    else:
        stream_id = frame.get("stream_id")
        if (
            not isinstance(stream_id, int)
            or isinstance(stream_id, bool)
            or not 0 <= stream_id <= 255
        ):
            error(f"frame.stream_id must be an integer 0-255, got {stream_id!r}")
        endianness = frame.get("endianness", "little")
        if endianness not in ENDIANNESS:
            error(f"frame.endianness must be 'little' or 'big', got {endianness!r}")
        if "pattern" in frame:
            warning("frame.pattern is for text-line profiles; ignored in a binary profile")

    fields = frame.get("fields")
    if not isinstance(fields, list) or not fields:
        error("frame.fields must be a non-empty list")
        return problems

    names: list[str] = []
    layout = ""
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
            layout += STRUCT_TYPE_MAP[ftype][0]

    if text:
        problems.extend(_pattern_problems(key, frame.get("pattern"), names))
    elif (payload := struct.calcsize("<" + layout)) > MAX_PAYLOAD_BYTES:
        error(f"payload is {payload} B; the protocol allows at most {MAX_PAYLOAD_BYTES} B")

    if text:
        pass  # a counter is optional: without one the X axis is the line number
    elif LOOP_CNTR_NAME not in names:
        error(f"frame must contain '{LOOP_CNTR_NAME}' (it's used to detect lost frames)")
    else:
        if names[0] != LOOP_CNTR_NAME:
            warning(f"'{LOOP_CNTR_NAME}' is conventionally the first field")
        cntr = next(f for f in fields if isinstance(f, dict) and f.get("name") == LOOP_CNTR_NAME)
        if cntr.get("type") != "u32":
            warning(f"'{LOOP_CNTR_NAME}' should be u32, got {cntr.get('type')!r}")

    time_names = [*names, LINE_FIELD] if text else names
    default_field = default_time_field(stream) if text else LOOP_CNTR_NAME
    problems.extend(_time_problems(key, stream.get("time"), time_names, default_field))
    problems.extend(_sim_problems(key, stream.get("sim"), names))
    problems.extend(_lane_problems(key, stream))

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


def _pattern_problems(key: str, pattern: Any, names: list[str]) -> list[ConfigProblem]:
    """A text stream's `frame.pattern` (R8.3): valid, and its slots are the fields in order."""

    def error(msg: str) -> list[ConfigProblem]:
        return [ConfigProblem("error", key, msg)]

    if not isinstance(pattern, str) or not pattern.strip():
        return error('frame.pattern is required for a text stream (e.g. "IMU,{ms},{ax}")')
    try:
        slots = list(parse_pattern(pattern).slots)
    except PatternError as e:
        return error(f"frame.pattern: {e}")
    if slots != names:
        return error(
            f"frame.pattern slots ({', '.join(slots)}) must be the fields in order "
            f"({', '.join(names)})"
        )
    return []


def _time_problems(
    key: str, time_cfg: Any, names: list[str], default_field: str = LOOP_CNTR_NAME
) -> list[ConfigProblem]:
    """Checks the optional `time: {field, scale_s, step}` block (R2.5)."""
    if time_cfg is None:
        return []
    if not isinstance(time_cfg, dict):
        return [ConfigProblem("error", key, "'time' must be an object")]
    problems: list[ConfigProblem] = []
    field = time_cfg.get("field", default_field)
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
    if field not in (LOOP_CNTR_NAME, LINE_FIELD) and "step" not in time_cfg:
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


def _y_range_problems(where: str, y_range: Any) -> list[str]:
    if not isinstance(y_range, dict):
        return [f"{where}.y_range must be an object"]
    msgs = []
    mode = y_range.get("mode", "auto")
    if mode not in Y_RANGE_MODES:
        msgs.append(f"{where}.y_range.mode {mode!r} is unknown (known: {', '.join(Y_RANGE_MODES)})")
    lo, hi = y_range.get("min"), y_range.get("max")
    for name, value in (("min", lo), ("max", hi)):
        if value is not None and (not isinstance(value, int | float) or isinstance(value, bool)):
            msgs.append(f"{where}.y_range.{name} must be a number")
    if isinstance(lo, int | float) and isinstance(hi, int | float) and not lo < hi:
        msgs.append(f"{where}.y_range needs min < max")
    if "include_zero" in y_range and not isinstance(y_range["include_zero"], bool):
        msgs.append(f"{where}.y_range.include_zero must be true or false")
    unknown = sorted(set(y_range) - set(Y_RANGE_KEYS))
    if unknown:
        msgs.append(f"{where}.y_range: unknown key(s) {', '.join(unknown)}")
    return msgs


def _lane_problems(key: str, stream: dict[str, Any]) -> list[ConfigProblem]:
    """
    Checks lanes (R3.1): `groups` and each signal's `group` and `y_range`. They only
    affect display, so every problem is a warning, and the plot falls back to defaults.
    """
    msgs: list[str] = []
    groups = stream.get("groups", {})
    if not isinstance(groups, dict):
        msgs.append("'groups' must be an object")
        groups = {}
    for name, group in groups.items():
        if not isinstance(group, dict):
            msgs.append(f"groups.{name} must be an object")
            continue
        if "label" in group and not isinstance(group["label"], str):
            msgs.append(f"groups.{name}.label must be a string")
        order = group.get("order")
        if order is not None and (not isinstance(order, int | float) or isinstance(order, bool)):
            msgs.append(f"groups.{name}.order must be a number")
        if "y_range" in group:
            msgs.extend(_y_range_problems(f"groups.{name}", group["y_range"]))
        unknown = sorted(set(group) - set(GROUP_KEYS))
        if unknown:
            msgs.append(f"groups.{name}: unknown key(s) {', '.join(unknown)}")
    signals = stream.get("signals", {})
    for sig_key, sig in signals.items() if isinstance(signals, dict) else ():
        if not isinstance(sig, dict):
            continue
        if "group" in sig and not isinstance(sig["group"], str):
            msgs.append(f"signal '{sig_key}': group must be a string")
        if "y_range" in sig:
            msgs.extend(_y_range_problems(f"signal '{sig_key}'", sig["y_range"]))
    return [ConfigProblem("warning", key, msg) for msg in msgs]
