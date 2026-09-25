"""
Line patterns from console output (R8.4): "From console output…" in the editor.

The firmware prints lines in whatever layout its author chose, with no header. Pasting
(or listening to) a few seconds of it is enough to find the layouts:

1. Each line is split into numbers and the fixed text between them. The fixed text is
   the line's **shape**: `IMU,1204,0.12` and `IMU,1214,0.13` share `("IMU,", ",", "")`.
2. Lines are grouped by shape. A shape seen once (a boot banner) is not a pattern.
3. A value is named from the text before it when that ends in `name=` or `name:`
   (`t=24.5` gives `t`), else `v1`, `v2`… by position.
4. A value whose every sample is a whole number is an integer (`u32`, or `i32` when a
   negative was seen); otherwise a number (`f32`).
5. The first integer that goes up by a (nearly) constant step is suggested as the X axis,
   with that step; its time per tick is 1 ms when its name says `ms`, else the default.

Each pattern becomes a stream: its fields, the pattern, a signal per value except the X
axis, lanes left to the defaults. `TextLineDecoder` decodes the lines it came from.

Pure Python, no Qt.
"""

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from core.config.draft import StreamDraft, unique_name
from core.protocol.text_line import Slot, pattern_text

# A number in console output. Unlike a slot's, it can't start inside a word or another
# number (`v1=`, `fw 1.4.2`'s `.2`), and `nan`/`inf` must be whole words (`info`).
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_.])"
    r"(?:[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|[-+]?(?i:nan|infinity|inf)(?![A-Za-z]))"
)
_NAME_BEFORE = re.compile(r"([A-Za-z_]\w*)\s*[=:]\s*$")
_FIRST_WORD = re.compile(r"[A-Za-z_]\w*")
_U32_MAX = 2**32 - 1
_I32_MIN = -(2**31)
MS_SCALE_S = 0.001
AXIS_TOLERANCE = 0.5  # a step within ±50 % of the median still counts as constant


@dataclass(frozen=True)
class InferredValue:
    name: str
    type: str
    lo: float
    hi: float
    axis_step: float | None = None
    """Set on the suggested X axis: its step per line."""

    @property
    def range_text(self) -> str:
        lo, hi = _short(self.lo), _short(self.hi)
        return lo if lo == hi else f"{lo} … {hi}"

    @property
    def note(self) -> str:
        return f"X axis, step {_short(self.axis_step)}" if self.axis_step is not None else ""


@dataclass
class InferredPattern:
    name: str
    """The stream name: the first word of the fixed text, else `stream N`."""
    pattern: str
    tokens: tuple[str | Slot, ...]
    values: list[InferredValue]
    lines: list[str] = field(default_factory=list)

    def stream(self) -> dict[str, Any]:
        """The stream to create: fields, pattern, time axis and a signal per plain value."""
        fields = [{"name": v.name, "type": v.type} for v in self.values]
        draft = StreamDraft(
            {"name": self.name, "frame": {"pattern": self.pattern, "fields": fields}}
        )
        axis = next((v for v in self.values if v.axis_step is not None), None)
        if axis is not None:
            draft.set_time("field", axis.name)
            draft.set_time("step", _plain(axis.axis_step))
            if re.search(r"ms|millis", axis.name, re.IGNORECASE):
                draft.set_time("scale_s", MS_SCALE_S)
        for value in self.values:
            if value is not axis:
                draft.add_signal(value.name)
        return draft.to_stream()


@dataclass
class Inference:
    patterns: list[InferredPattern]
    seen_once: list[str]
    line_count: int


def split_line(line: str) -> tuple[tuple[str, ...], list[str]]:
    """A line's shape (the fixed text around its numbers) and its numbers, as text."""
    pieces: list[str] = []
    numbers: list[str] = []
    pos = 0
    for m in _NUMBER.finditer(line):
        pieces.append(line[pos : m.start()])
        numbers.append(m.group())
        pos = m.end()
    pieces.append(line[pos:])
    return tuple(pieces), numbers


def infer_patterns(lines: Iterable[str]) -> Inference:
    groups: dict[tuple[str, ...], list[tuple[str, list[str]]]] = {}
    count = 0
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        count += 1
        shape, numbers = split_line(line)
        groups.setdefault(shape, []).append((line, numbers))

    patterns: list[InferredPattern] = []
    seen_once: list[str] = []
    for shape, members in groups.items():  # dicts keep first appearance
        if len(members) < 2 or len(shape) < 2:
            seen_once.extend(line for line, _ in members)
            continue
        patterns.append(_pattern(shape, members, len(patterns) + 1))
    taken: set[str] = set()
    for p in patterns:  # distinct stream names: IMU, IMU 2
        name, n = p.name, 2
        while name in taken:
            name, n = f"{p.name} {n}", n + 1
        p.name = name
        taken.add(name)
    return Inference(patterns, seen_once, count)


def _pattern(
    shape: tuple[str, ...], members: list[tuple[str, list[str]]], number: int
) -> InferredPattern:
    names: list[str] = []
    for i, before in enumerate(shape[:-1]):
        m = _NAME_BEFORE.search(before)
        name = m.group(1) if m else f"v{i + 1}"
        names.append(unique_name(name, set(names)))
    columns = [[float(nums[i]) for _, nums in members] for i in range(len(names))]
    values: list[InferredValue] = []
    has_axis = False
    for name, column in zip(names, columns, strict=True):
        vtype = _value_type(column)
        step = None if has_axis or vtype == "f32" else _axis_step(column)
        has_axis = has_axis or step is not None
        finite = [v for v in column if math.isfinite(v)] or [math.nan]
        values.append(InferredValue(name, vtype, min(finite), max(finite), step))

    tokens: list[str | Slot] = []
    for i, text in enumerate(shape):
        if text:
            tokens.append(text)
        if i < len(names):
            tokens.append(Slot(names[i]))
    first = _FIRST_WORD.match(shape[0])
    return InferredPattern(
        name=first.group() if first else f"stream {number}",
        pattern=pattern_text(tokens),
        tokens=tuple(tokens),
        values=values,
        lines=[line for line, _ in members],
    )


def _value_type(column: list[float]) -> str:
    if not all(math.isfinite(v) and v.is_integer() for v in column):
        return "f32"
    if min(column) < 0:
        return "i32" if min(column) >= _I32_MIN and max(column) <= 2**31 - 1 else "f32"
    return "u32" if max(column) <= _U32_MAX else "f32"


def _axis_step(column: list[float]) -> float | None:
    """The step of a column that rises by a (nearly) constant amount, else None."""
    if len(column) < 2:
        return None
    diffs = [b - a for a, b in zip(column, column[1:], strict=False)]
    if min(diffs) <= 0:
        return None
    step = statistics.median(diffs)
    lo, hi = step * (1 - AXIS_TOLERANCE), step * (1 + AXIS_TOLERANCE)
    return step if all(lo <= d <= hi for d in diffs) else None


def _plain(value: float | None) -> int | float:
    """10.0 is written as 10 in the file."""
    if value is None:
        return 1
    return int(value) if float(value).is_integer() else value


def _short(value: float | None) -> str:
    return "" if value is None else f"{value:g}"
