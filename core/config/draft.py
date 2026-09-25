"""
A stream definition being edited (R7.1): the configuration editor's model.

The editor used to keep a stream in its widgets and rebuild the dict on every switch,
which is how edits and unknown keys got lost (C4). A `StreamDraft` holds the stream dict
itself, and every operation changes only the keys it's about. Everything else (unknown
keys, key order, number formatting) is carried through untouched, so a stream that isn't
edited saves byte-identically.

Pure Python, no Qt: the widgets in `ui/config` call these operations and redraw.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.acquisition.timebase import DEFAULT_SCALE_S
from core.protocol.constants import LOOP_CNTR_NAME, STRUCT_TYPE_MAP

# The dashboard's signal colors, in the order new signals take them.
PALETTE = (
    "#4FC3F7",
    "#81C784",
    "#FFB74D",
    "#BA68C8",
    "#E57373",
    "#64B5F6",
    "#F46DE9",
    "#FFF176",
    "#4DB6AC",
    "#A1887F",
)
LINE_STYLES = ("solid", "dashed", "dotted")
LINE_WIDTH = 1  # 1 px draws fastest (P4)
TIME_DEFAULTS: dict[str, Any] = {"field": LOOP_CNTR_NAME, "scale_s": DEFAULT_SCALE_S, "step": 1}


@dataclass(frozen=True)
class FieldSlot:
    """Where a frame field sits in the payload. An unknown type takes no bytes."""

    index: int
    name: str
    type: str
    offset: int
    size: int


def type_size(type_name: Any) -> int:
    return STRUCT_TYPE_MAP[type_name][1] if type_name in STRUCT_TYPE_MAP else 0


def unique_name(base: str, taken: set[str] | dict[str, Any], fallback: str = "field") -> str:
    """`base` made an identifier, with `_2`, `_3`… appended until it's free."""
    stem = re.sub(r"\W", "_", base.strip()) or fallback
    name, n = stem, 2
    while name in taken:
        name, n = f"{stem}_{n}", n + 1
    return name


class StreamDraft:
    """One stream's definition, edited in place. `to_stream()` returns a copy to save."""

    def __init__(self, stream: Mapping[str, Any]) -> None:
        self.data: dict[str, Any] = copy.deepcopy(dict(stream))

    def to_stream(self) -> dict[str, Any]:
        return copy.deepcopy(self.data)

    # --- stream properties ---

    @property
    def name(self) -> str:
        return str(self.data.get("name", ""))

    @name.setter
    def name(self, value: str) -> None:
        self.data["name"] = value

    def _frame(self) -> dict[str, Any]:
        frame = self.data.get("frame")
        if not isinstance(frame, dict):
            frame = self.data["frame"] = {}
        return frame

    @property
    def stream_id(self) -> int:
        value = self._frame().get("stream_id", 0)
        return value if isinstance(value, int) else 0

    @stream_id.setter
    def stream_id(self, value: int) -> None:
        self._frame()["stream_id"] = value

    @property
    def endianness(self) -> str:
        return str(self._frame().get("endianness", "little"))

    @endianness.setter
    def endianness(self, value: str) -> None:
        self._frame()["endianness"] = value

    @property
    def controls(self) -> str | None:
        value = self.data.get("controls")
        return value if isinstance(value, str) else None

    @controls.setter
    def controls(self, value: str | None) -> None:
        if value is None:
            self.data.pop("controls", None)
        else:
            self.data["controls"] = value

    # --- time base (R2.5) ---

    def time_value(self, key: str) -> Any:
        time_cfg = self.data.get("time")
        if isinstance(time_cfg, dict) and key in time_cfg:
            return time_cfg[key]
        return TIME_DEFAULTS[key]

    def set_time(self, key: str, value: Any) -> None:
        """Writes a time key only if the file had it or the value isn't the default."""
        time_cfg = self.data.get("time")
        if isinstance(time_cfg, dict) and key in time_cfg:
            time_cfg[key] = value
        elif value != TIME_DEFAULTS[key]:
            if not isinstance(time_cfg, dict):
                time_cfg = self.data["time"] = {}
            time_cfg[key] = value

    # --- frame fields ---

    @property
    def fields(self) -> list[dict[str, Any]]:
        fields = self._frame().get("fields")
        if not isinstance(fields, list):
            fields = self._frame()["fields"] = []
        return fields

    def field_names(self) -> list[str]:
        return [str(f.get("name", "")) for f in self.fields if isinstance(f, dict)]

    def layout(self) -> list[FieldSlot]:
        slots: list[FieldSlot] = []
        offset = 0
        for i, f in enumerate(self.fields):
            if not isinstance(f, dict):
                continue
            size = type_size(f.get("type"))
            slots.append(FieldSlot(i, str(f.get("name", "")), str(f.get("type", "")), offset, size))
            offset += size
        return slots

    def payload_size(self) -> int:
        return sum(s.size for s in self.layout())

    def add_field(self, index: int, name: str = "field", type_name: str = "f32") -> int:
        """Inserts a field at `index` (a free name made from `name`); returns its index."""
        index = max(0, min(index, len(self.fields)))
        self.fields.insert(
            index, {"name": unique_name(name, set(self.field_names())), "type": type_name}
        )
        return index

    def remove_field(self, index: int) -> None:
        """Removes a field and the signals drawn from it."""
        name = self.field_names()[index]
        del self.fields[index]
        for key in self.signals_of_field(name):
            self.remove_signal(key)
        sim_fields = self._sim_fields()
        if sim_fields is not None:
            sim_fields.pop(name, None)

    def rename_field(self, index: int, new_name: str) -> str | None:
        """Renames a field and everything that refers to it; returns why not, or None."""
        new_name = new_name.strip()
        old = self.field_names()[index]
        if new_name == old:
            return None
        if not re.fullmatch(r"[A-Za-z_]\w*", new_name):
            return f"'{new_name}' is not a valid name (letters, digits and _)"
        if new_name in self.field_names():
            return f"there is already a field '{new_name}'"
        self.fields[index]["name"] = new_name
        for sig in self._signal_dicts().values():
            if sig.get("field") == old:
                sig["field"] = new_name
        time_cfg = self.data.get("time")
        if isinstance(time_cfg, dict) and time_cfg.get("field") == old:
            time_cfg["field"] = new_name
        sim_fields = self._sim_fields()
        if sim_fields is not None and old in sim_fields:
            renamed = {(new_name if k == old else k): v for k, v in sim_fields.items()}
            sim_fields.clear()
            sim_fields.update(renamed)
        return None

    def set_field_type(self, index: int, type_name: str) -> None:
        self.fields[index]["type"] = type_name

    def move_field(self, index: int, to: int) -> None:
        to = max(0, min(to, len(self.fields) - 1))
        if to != index:
            self.fields.insert(to, self.fields.pop(index))

    def _sim_fields(self) -> dict[str, Any] | None:
        sim = self.data.get("sim")
        fields = sim.get("fields") if isinstance(sim, dict) else None
        return fields if isinstance(fields, dict) else None

    # --- signals ---

    def _signal_dicts(self) -> dict[str, dict[str, Any]]:
        signals = self.data.get("signals")
        if not isinstance(signals, dict):
            return {}
        return {k: v for k, v in signals.items() if isinstance(v, dict)}

    @property
    def signals(self) -> dict[str, Any]:
        signals = self.data.get("signals")
        if not isinstance(signals, dict):
            signals = self.data["signals"] = {}
        return signals

    def signals_of_field(self, field: str) -> list[str]:
        return [k for k, s in self._signal_dicts().items() if s.get("field") == field]

    def unused_color(self) -> str:
        used = {str(s.get("color", "")).lower() for s in self._signal_dicts().values()}
        free = [c for c in PALETTE if c.lower() not in used]
        return free[0] if free else PALETTE[len(self._signal_dicts()) % len(PALETTE)]

    def add_signal(self, field: str, lane: str | None = None) -> str:
        """Plots a field: a new signal labelled with the field's name; returns its key."""
        key = unique_name(field, self.signals, fallback="signal")
        signal: dict[str, Any] = {"label": field, "field": field}
        if lane:
            signal["group"] = lane
        signal["color"] = self.unused_color()
        signal["line"] = {"style": "solid", "width": LINE_WIDTH}
        signal["visible"] = True
        self.signals[key] = signal
        return key

    def remove_signal(self, key: str) -> None:
        self.signals.pop(key, None)

    def signal(self, key: str) -> dict[str, Any]:
        sig = self.signals.get(key)
        return sig if isinstance(sig, dict) else {}

    def set_signal(self, key: str, attr: str, value: Any) -> None:
        """Sets label, color, visible, group ("" = the default lane), style or width."""
        sig = self.signals[key]
        if attr in ("style", "width"):
            line = sig.get("line")
            if not isinstance(line, dict):
                line = sig["line"] = {}
            line[attr] = value
        elif attr == "group" and not value:
            sig.pop("group", None)
        else:
            sig[attr] = value

    # --- lanes ---

    def lane_label(self, lane: str) -> str | None:
        groups = self.data.get("groups")
        group = groups.get(lane) if isinstance(groups, dict) else None
        label = group.get("label") if isinstance(group, dict) else None
        return label if isinstance(label, str) else None

    def set_lane_label(self, lane: str, label: str) -> None:
        """Names a lane (its `groups` entry, created last in order if it didn't exist)."""
        groups = self.data.get("groups")
        if not isinstance(groups, dict):
            groups = self.data["groups"] = {}
        group = groups.get(lane)
        if not isinstance(group, dict):
            orders = [
                g["order"]
                for g in groups.values()
                if isinstance(g, dict) and isinstance(g.get("order"), int | float)
            ]
            groups[lane] = {"label": label, "order": max(orders, default=0) + 1}
        else:
            group["label"] = label
