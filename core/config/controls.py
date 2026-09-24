"""
Commands and control panels from `streams.json` (R5.2): parsed into typed definitions.

```json
"commands": {
    "pid_single": {
        "label": "PID gains, one motor", "packet_id": 16, "endianness": "little",
        "fields": [
            {"name": "motor_id", "type": "u8"},
            {"name": "kp", "type": "f32", "param": "kp"}
        ]
    }
},
"panels": {
    "diffbot_pid": {
        "title": "PID Tuning",
        "columns": ["Left", "Right"],
        "parameters": {"kp": {"label": "Kp", "kind": "float", "default": 0.1}},
        "buttons": [
            {"label": "Update Left", "command": "pid_single", "column": "Left",
             "values": {"motor_id": 0}}
        ]
    }
}
```

A stream shows a panel with `"controls": "<panel key>"`. A panel is a grid: one row per
parameter, one column per `columns` entry (a single unnamed column when there are none).
Each button sends a command, whose fields take their values as described in
`core.protocol.commands`.

A command or panel with errors is left out (its problems are reported), never the whole
file: telemetry doesn't depend on them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from core.config.streams import ENDIANNESS, MAX_PAYLOAD_BYTES, ConfigProblem
from core.protocol.commands import CommandDef, CommandField
from core.protocol.constants import STRUCT_TYPE_MAP
from core.protocol.record_decoder import frame_dtype
from core.types import StreamFrameField

PARAM_KINDS = ("float", "int", "bool")
COMMAND_KEYS = ("label", "packet_id", "endianness", "fields")
COMMAND_FIELD_KEYS = ("name", "type", "value", "param", "column")
PANEL_KEYS = ("title", "columns", "parameters", "buttons")
PARAM_KEYS = ("label", "kind", "default", "min", "max", "step", "decimals")
BUTTON_KEYS = ("label", "command", "column", "values")
IMPLICIT_COLUMN = ""  # the single column of a panel without `columns`


@dataclass(frozen=True)
class ParamDef:
    key: str
    label: str
    kind: str = "float"
    default: float = 0.0
    minimum: float = -1000.0
    maximum: float = 1000.0
    step: float = 0.01
    decimals: int = 4


@dataclass(frozen=True)
class ButtonDef:
    label: str
    command: str
    column: str | None = None
    values: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class PanelDef:
    key: str
    title: str
    columns: tuple[str, ...]
    parameters: tuple[ParamDef, ...]
    buttons: tuple[ButtonDef, ...]

    @property
    def column_keys(self) -> tuple[str, ...]:
        return self.columns or (IMPLICIT_COLUMN,)

    def button_column(self, button: ButtonDef) -> str | None:
        """The column a button's parameters come from (the implicit one if there are none)."""
        return button.column if self.columns else IMPLICIT_COLUMN

    def defaults(self) -> dict[str, dict[str, float]]:
        return {col: {p.key: p.default for p in self.parameters} for col in self.column_keys}


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _text(value: Any, default: str) -> str:
    return value if isinstance(value, str) else default


def _unknown(obj: dict[str, Any], known: tuple[str, ...], where: str) -> list[str]:
    extra = sorted(set(obj) - set(known))
    return [f"{where}: unknown key(s) {', '.join(extra)}"] if extra else []


def parse_commands(raw: Any) -> tuple[dict[str, CommandDef], list[ConfigProblem]]:
    """The valid commands, and every problem found (errors exclude a command)."""
    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, [ConfigProblem("error", None, "'commands' must be an object", "commands")]
    commands: dict[str, CommandDef] = {}
    problems: list[ConfigProblem] = []
    for key, spec in raw.items():
        item = f"command '{key}'"
        errors, warnings = _command_problems(spec)
        problems += [ConfigProblem("error", None, m, item) for m in errors]
        problems += [ConfigProblem("warning", None, m, item) for m in warnings]
        if not errors:
            commands[str(key)] = _command(str(key), spec)
    return commands, problems


def _command_problems(spec: Any) -> tuple[list[str], list[str]]:
    if not isinstance(spec, dict):
        return ["must be an object"], []
    errors: list[str] = []
    warnings = _unknown(spec, COMMAND_KEYS, "command")
    pid = spec.get("packet_id")
    if not isinstance(pid, int) or isinstance(pid, bool) or not 0 <= pid <= 255:
        errors.append(f"packet_id must be an integer 0-255, got {pid!r}")
    if spec.get("endianness", "little") not in ENDIANNESS:
        errors.append(f"endianness must be 'little' or 'big', got {spec.get('endianness')!r}")
    if "label" in spec and not isinstance(spec["label"], str):
        warnings.append("label must be a string")
    fields = spec.get("fields", [])
    if not isinstance(fields, list):
        return [*errors, "fields must be a list"], warnings
    names: set[str] = set()
    for i, f in enumerate(fields):
        if not isinstance(f, dict) or not isinstance(f.get("name"), str) or not f["name"]:
            errors.append(f"fields[{i}] needs a non-empty 'name'")
            continue
        name = f["name"]
        if name in names:
            errors.append(f"duplicate field '{name}'")
        names.add(name)
        if f.get("type") not in STRUCT_TYPE_MAP:
            errors.append(f"field '{name}' has unknown type {f.get('type')!r}")
        if "value" in f and not _is_number(f["value"]):
            errors.append(f"field '{name}': value must be a number")
        if "param" in f and (not isinstance(f["param"], str) or not f["param"]):
            errors.append(f"field '{name}': param must be a parameter key")
        if "value" in f and "param" in f:
            errors.append(f"field '{name}' has both a value and a param")
        if "column" in f and not isinstance(f["column"], str):
            errors.append(f"field '{name}': column must be a string")
        if "column" in f and "param" not in f:
            warnings.append(f"field '{name}': column is only used with a param")
        warnings += _unknown(f, COMMAND_FIELD_KEYS, f"field '{name}'")
    if not errors:
        layout: list[StreamFrameField] = [{"name": f["name"], "type": f["type"]} for f in fields]
        size = frame_dtype(spec.get("endianness", "little"), layout).itemsize
        if size > MAX_PAYLOAD_BYTES:
            errors.append(f"payload is {size} B; the protocol allows at most {MAX_PAYLOAD_BYTES} B")
    return errors, warnings


def _command(key: str, spec: dict[str, Any]) -> CommandDef:
    fields = tuple(
        CommandField(
            name=f["name"],
            type=f["type"],
            value=float(f["value"]) if "value" in f else None,
            param=f.get("param"),
            column=f.get("column"),
        )
        for f in spec.get("fields", [])
    )
    return CommandDef(
        key=key,
        label=_text(spec.get("label"), key),
        packet_id=spec["packet_id"],
        fields=fields,
        endianness=spec.get("endianness", "little"),
    )


def parse_panels(
    raw: Any, commands: Mapping[str, CommandDef], declared_commands: set[str]
) -> tuple[dict[str, PanelDef], list[ConfigProblem]]:
    """
    The valid panels, and every problem found. `commands` are the valid commands;
    `declared_commands` are all command keys in the file, to tell "has errors" from "missing".
    """
    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, [ConfigProblem("error", None, "'panels' must be an object", "panels")]
    panels: dict[str, PanelDef] = {}
    problems: list[ConfigProblem] = []
    for key, spec in raw.items():
        item = f"panel '{key}'"
        errors, warnings, panel = _panel(str(key), spec, commands, declared_commands)
        problems += [ConfigProblem("error", None, m, item) for m in errors]
        problems += [ConfigProblem("warning", None, m, item) for m in warnings]
        if panel is not None and not errors:
            panels[str(key)] = panel
    return panels, problems


def _panel(
    key: str, spec: Any, commands: Mapping[str, CommandDef], declared: set[str]
) -> tuple[list[str], list[str], PanelDef | None]:
    if not isinstance(spec, dict):
        return ["must be an object"], [], None
    errors: list[str] = []
    warnings = _unknown(spec, PANEL_KEYS, "panel")

    columns = spec.get("columns", [])
    if not isinstance(columns, list) or not all(isinstance(c, str) and c for c in columns):
        errors.append("columns must be a list of non-empty strings")
        columns = []
    elif len(set(columns)) != len(columns):
        errors.append("columns must be unique")

    params: list[ParamDef] = []
    raw_params = spec.get("parameters", {})
    if not isinstance(raw_params, dict):
        errors.append("parameters must be an object")
        raw_params = {}
    for pkey, pspec in raw_params.items():
        param, perrors, pwarnings = _param(str(pkey), pspec)
        errors += perrors
        warnings += pwarnings
        if param is not None:
            params.append(param)
    by_key = {p.key: p for p in params}

    buttons: list[ButtonDef] = []
    raw_buttons = spec.get("buttons", [])
    if not isinstance(raw_buttons, list) or not raw_buttons:
        errors.append("buttons must be a non-empty list")
        raw_buttons = []
    for i, bspec in enumerate(raw_buttons):
        button, berrors, bwarnings = _button(i, bspec, columns, by_key, commands, declared)
        errors += berrors
        warnings += bwarnings
        if button is not None:
            buttons.append(button)

    title = _text(spec.get("title"), key)
    panel = PanelDef(key, title, tuple(columns), tuple(params), tuple(buttons))
    return errors, warnings, panel


def _param(key: str, spec: Any) -> tuple[ParamDef | None, list[str], list[str]]:
    where = f"parameter '{key}'"
    if not isinstance(spec, dict):
        return None, [f"{where} must be an object"], []
    errors: list[str] = []
    warnings = _unknown(spec, PARAM_KEYS, where)
    kind = spec.get("kind", "float")
    if kind not in PARAM_KINDS:
        return None, [f"{where}: kind must be one of {', '.join(PARAM_KINDS)}"], warnings
    for name in ("default", "min", "max", "step"):
        value = spec.get(name)
        if value is None or (name == "default" and kind == "bool" and isinstance(value, bool)):
            continue
        if not _is_number(value):
            errors.append(f"{where}: {name} must be a number")
    decimals = spec.get("decimals", 4)
    if not isinstance(decimals, int) or isinstance(decimals, bool) or not 0 <= decimals <= 10:
        errors.append(f"{where}: decimals must be an integer 0-10")
    if errors:
        return None, errors, warnings
    integral = kind != "float"
    lo = float(spec.get("min", 0 if kind == "bool" else -1000))
    hi = float(spec.get("max", 1 if kind == "bool" else 1000))
    step = float(spec.get("step", 1 if integral else 0.01))
    default = float(spec.get("default", 0))
    if not lo < hi:
        return None, [f"{where}: needs min < max"], warnings
    if step <= 0:
        return None, [f"{where}: step must be positive"], warnings
    if not lo <= default <= hi:
        warnings.append(f"{where}: default {default:g} is outside {lo:g}..{hi:g}; clamped")
        default = min(max(default, lo), hi)
    if integral and default != int(default):
        errors.append(f"{where}: default must be a whole number for kind '{kind}'")
    label = _text(spec.get("label"), key)
    param = ParamDef(key, label, kind, default, lo, hi, step, 0 if integral else decimals)
    return (None if errors else param), errors, warnings


def _button(
    i: int,
    spec: Any,
    columns: list[str],
    params: Mapping[str, ParamDef],
    commands: Mapping[str, CommandDef],
    declared: set[str],
) -> tuple[ButtonDef | None, list[str], list[str]]:
    where = f"buttons[{i}]"
    if not isinstance(spec, dict):
        return None, [f"{where} must be an object"], []
    errors: list[str] = []
    warnings = _unknown(spec, BUTTON_KEYS, where)
    label = spec.get("label")
    if not isinstance(label, str) or not label:
        errors.append(f"{where} needs a label")
        label = where
    where = f"button '{label}'"
    ckey = spec.get("command")
    command = commands.get(ckey) if isinstance(ckey, str) else None
    if command is None:
        reason = "has errors" if ckey in declared else "is not defined"
        return None, [*errors, f"{where}: command {ckey!r} {reason}"], warnings
    column = spec.get("column")
    if column is not None and column not in columns:
        errors.append(f"{where}: column {column!r} is not one of the panel's columns")
    values = spec.get("values", {})
    if not isinstance(values, dict) or not all(_is_number(v) for v in values.values()):
        errors.append(f"{where}: values must map field names to numbers")
        values = {}
    field_names = {f.name for f in command.fields}
    for name in values:
        if name not in field_names:
            errors.append(f"{where}: {name!r} is not a field of command '{ckey}'")
    # Every field must get a value when this button is pressed.
    for f in command.fields:
        if f.name in values or f.value is not None:
            continue
        if f.param is None:
            errors.append(f"{where}: field '{f.name}' of '{ckey}' gets no value")
            continue
        if f.param not in params:
            errors.append(f"{where}: field '{f.name}' uses unknown parameter '{f.param}'")
            continue
        col = f.column if f.column is not None else column
        if columns and col is None:
            errors.append(f"{where}: field '{f.name}' needs a column (the button has none)")
        elif f.column is not None and f.column not in columns:
            errors.append(f"{where}: field '{f.name}' uses unknown column {f.column!r}")
        integer_field = not STRUCT_TYPE_MAP[f.type][2].startswith("float")
        if integer_field and params[f.param].kind == "float":
            warnings.append(
                f"{where}: float parameter '{f.param}' feeds integer field '{f.name}'; "
                "non-whole values will be refused"
            )
    if errors:
        return None, errors, warnings
    button = ButtonDef(label, str(ckey), column, {k: float(v) for k, v in values.items()})
    return button, errors, warnings
