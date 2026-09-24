"""
`streams.json` schema versions and migrations (R5.1).

- **1** (no `schema_version`): streams only. A stream's `panel_type` ("pid", "imu",
  "none") picked a hard-coded panel. The PID panel sent the DiffBot packets 0x10/0x11
  with layouts in Python constants.
- **2**: `schema_version`, `commands` and `panels` at the top level (see
  `core.config.controls`). A stream shows a panel with `controls`. The DiffBot PID panel
  is an ordinary config entry, `DIFFBOT_*` below, byte-for-byte the packets version 1 sent.

`migrate()` upgrades a parsed document in memory, step by step. Nothing is written: the
file changes only when the user saves it from the Configuration tab.
"""

from __future__ import annotations

import copy
from typing import Any

SCHEMA_VERSION = 2

_GAINS: list[tuple[str, str, dict[str, Any]]] = [
    ("kp", "Kp", {"default": 0.1}),
    ("ki", "Ki", {"default": 0.02, "decimals": 5, "step": 0.001}),
    ("k1", "K1", {"default": 26.5, "step": 0.1}),
    ("k2", "K2", {"default": 8.0, "step": 0.1}),
    ("k3", "K3", {"default": 5.0, "step": 0.1}),
    ("k_aw", "Kaw", {"default": 1.0}),
    ("alpha", "Alpha", {"default": 0.2, "min": 0.0, "max": 1.0}),
    ("rps", "Rps", {"default": 0.3, "min": -50.0, "max": 50.0}),
]
_FLAGS = [("use_ramp", "Use Ramp"), ("use_pi", "Use PI")]


def _motor_fields(prefix: str = "", column: str | None = None) -> list[dict[str, Any]]:
    """kp..rps (f32), use_ramp, use_pi (u8): one motor's part of the packets."""
    fields: list[dict[str, Any]] = []
    for key, _, _ in _GAINS:
        fields.append({"name": prefix + key, "type": "f32", "param": key})
    for key, _ in _FLAGS:
        fields.append({"name": prefix + key, "type": "u8", "param": key})
    if column is not None:
        for f in fields:
            f["column"] = column
    return fields


DIFFBOT_COMMANDS: dict[str, Any] = {
    "pid_single": {
        "label": "PID gains, one motor",
        "packet_id": 0x10,
        "endianness": "little",
        "fields": [{"name": "motor_id", "type": "u8"}, *_motor_fields()],
    },
    "pid_both": {
        "label": "PID gains, both motors",
        "packet_id": 0x11,
        "endianness": "little",
        "fields": [*_motor_fields("left_", "Left"), *_motor_fields("right_", "Right")],
    },
}

DIFFBOT_PANEL_KEY = "diffbot_pid"
DIFFBOT_PANEL: dict[str, Any] = {
    "title": "PID Tuning",
    "columns": ["Left", "Right"],
    "parameters": {
        **{
            key: {"label": label, "kind": "float", "min": -1000.0, "max": 1000.0, **spec}
            for key, label, spec in _GAINS
        },
        **{key: {"label": label, "kind": "bool", "default": False} for key, label in _FLAGS},
    },
    "buttons": [
        {
            "label": "Update Left PID",
            "command": "pid_single",
            "column": "Left",
            "values": {"motor_id": 0},
        },
        {
            "label": "Update Right PID",
            "command": "pid_single",
            "column": "Right",
            "values": {"motor_id": 1},
        },
        {"label": "Run Test (Both Motors)", "command": "pid_both"},
    ],
}


class SchemaError(ValueError):
    """The document's schema version can't be read by this version of the app."""


def schema_version(doc: dict[str, Any]) -> int:
    version = doc.get("schema_version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise SchemaError(f"schema_version must be a positive integer, got {version!r}")
    if version > SCHEMA_VERSION:
        raise SchemaError(
            f"schema_version {version} was written by a newer version of the app "
            f"(this one reads up to {SCHEMA_VERSION})"
        )
    return version


def migrate(doc: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """
    The document at the current schema version, and a note per change made. The input is
    not modified. Raises SchemaError for a version this app can't read.
    """
    version = schema_version(doc)
    out = copy.deepcopy(doc)
    notes: list[str] = []
    if version < 2:
        out, step_notes = _v1_to_v2(out)
        notes += step_notes
    return out, notes


def _v1_to_v2(doc: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    notes: list[str] = []
    streams = doc.get("streams")
    commands = dict(doc.get("commands") or {})
    panels = dict(doc.get("panels") or {})
    if isinstance(streams, dict):
        for key, stream in streams.items():
            if not isinstance(stream, dict) or "panel_type" not in stream:
                continue
            panel_type = stream["panel_type"]
            # `controls` takes `panel_type`'s place (or it's just removed).
            replaced = {
                ("controls" if k == "panel_type" else k): (
                    DIFFBOT_PANEL_KEY if k == "panel_type" else v
                )
                for k, v in stream.items()
                if k != "panel_type" or panel_type == "pid"
            }
            stream.clear()
            stream.update(replaced)
            if panel_type == "pid":
                for ckey, cmd in DIFFBOT_COMMANDS.items():
                    commands.setdefault(ckey, copy.deepcopy(cmd))
                panels.setdefault(DIFFBOT_PANEL_KEY, copy.deepcopy(DIFFBOT_PANEL))
                notes.append(f"stream '{key}': panel_type 'pid' -> controls '{DIFFBOT_PANEL_KEY}'")
            elif panel_type not in ("none", None):
                notes.append(
                    f"stream '{key}': panel_type '{panel_type}' dropped (it sent no commands)"
                )
    # schema_version first, the rest in its original order.
    rest = {k: v for k, v in doc.items() if k not in ("schema_version", "commands", "panels")}
    out: dict[str, Any] = {"schema_version": 2}
    if commands:
        out["commands"] = commands
    if panels:
        out["panels"] = panels
    out.update(rest)
    notes.insert(0, "migrated from schema version 1 to 2")
    return out, notes
