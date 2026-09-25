"""
Text-line streams (R8.3, ADR-0010): boards that print lines instead of binary frames.

A text stream's `frame.pattern` is one line layout: fixed text plus number slots, e.g.

    "IMU,{ms},{ax},{ay},{az}"      "ENV t={t}C h={h}%"

Grammar (one parser for the decoder, validation and the editor):
- `{name}` is a number slot; `name` is an identifier. `_line` is reserved (see below).
- Everything else is fixed text, matched literally. `{{` and `}}` are literal braces, and
  a run of whitespace matches one or more whitespace characters (printf padding).
- Two slots with no fixed text between them are an error (the boundary is ambiguous), as
  are a duplicate slot name and a pattern without slots.

A slot holds a number (sign, decimals, exponent, `nan`, `inf`) or nothing. For a float
field nothing (or `nan`) is NaN, a gap in the plot. An integer field needs an integral
value in its type's range; otherwise the line is dropped and counted in `value_errors`.

`TextLineDecoder` is the `LinkDecoder` for text profiles. Each record also carries
`_line: u32`, the stream's matched-line count since `reset()`, which is the X axis of a
stream without a counter. Nothing is dropped silently (C13): unmatched, overlong and
unconvertible lines are counted in the link statistics.

Pure Python + numpy, no Qt.
"""

from __future__ import annotations

import math
import re
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from core.protocol.constants import LOOP_CNTR_NAME, STRUCT_TYPE_MAP
from core.protocol.stats import LinkStats
from core.types import StreamConfig

LINE_FIELD = "_line"
"""The per-stream matched-line counter every text record carries."""
REPLIES_KEPT = 200
"""The newest unmatched lines kept for the terminal (R8.5): the board's replies."""
MAX_LINE_BYTES = 1024
"""Longer lines are dropped (and counted) so a board that never sends `\\n` can't grow the
buffer without bound."""
NUMBER_RE = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|[-+]?(?i:nan|infinity|inf)"
"""A number in a line: what a slot matches (besides nothing)."""

_SLOT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_WHITESPACE = re.compile(r"\s+")


class PatternError(ValueError):
    """A pattern that doesn't follow the grammar."""


@dataclass(frozen=True)
class Pattern:
    """A parsed line pattern: tokens in order, each fixed text (str) or a slot (`Slot`)."""

    text: str
    tokens: tuple[str | Slot, ...]
    regex: re.Pattern[str]

    @property
    def slots(self) -> tuple[str, ...]:
        return tuple(t.name for t in self.tokens if isinstance(t, Slot))


@dataclass(frozen=True)
class Slot:
    name: str


def parse_pattern(text: str) -> Pattern:
    """Parses a pattern; raises PatternError (a ValueError) saying what is wrong."""
    tokens: list[str | Slot] = []
    literal: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if text.startswith("{{", i) or text.startswith("}}", i):
            literal.append(ch)
            i += 2
        elif ch == "{":
            end = text.find("}", i)
            if end < 0:
                raise PatternError(f"unclosed '{{' at position {i + 1}")
            name = text[i + 1 : end]
            if not _SLOT_NAME.fullmatch(name):
                raise PatternError(f"'{{{name}}}' is not a slot: a name is letters, digits, _")
            if name == LINE_FIELD:
                raise PatternError(f"'{LINE_FIELD}' is reserved (the line number)")
            if literal:
                tokens.append("".join(literal))
                literal = []
            elif tokens and isinstance(tokens[-1], Slot):
                raise PatternError(
                    f"'{{{tokens[-1].name}}}' and '{{{name}}}' need fixed text between them"
                )
            tokens.append(Slot(name))
            i = end + 1
        elif ch == "}":
            raise PatternError(f"unmatched '}}' at position {i + 1} (write '}}}}' for a brace)")
        else:
            literal.append(ch)
            i += 1
    if literal:
        tokens.append("".join(literal))

    names = [t.name for t in tokens if isinstance(t, Slot)]
    if not names:
        raise PatternError("a pattern needs at least one {slot}")
    duplicate = next((name for k, name in enumerate(names) if name in names[:k]), None)
    if duplicate is not None:
        raise PatternError(f"slot '{{{duplicate}}}' appears twice")
    return Pattern(text, tuple(tokens), re.compile(_regex(tokens)))


def _regex(tokens: Sequence[str | Slot]) -> str:
    """Lines arrive stripped, so the pattern's leading and trailing whitespace is too."""
    parts: list[str] = []
    last = len(tokens) - 1
    for k, token in enumerate(tokens):
        if isinstance(token, Slot):
            parts.append(f"((?:{NUMBER_RE})?)")
            continue
        text = token.lstrip() if k == 0 else token
        text = text.rstrip() if k == last else text
        pieces = _WHITESPACE.split(text)
        parts.append(r"\s+".join(re.escape(p) for p in pieces))
    return "".join(parts)


def pattern_text(tokens: Sequence[str | Slot]) -> str:
    """Writes tokens back as a pattern (`{` and `}` in fixed text doubled): the editor's
    way to change a pattern (R8.4)."""
    return "".join(
        f"{{{t.name}}}" if isinstance(t, Slot) else t.replace("{", "{{").replace("}", "}}")
        for t in tokens
    )


def format_line(pattern: Pattern, values: Mapping[str, Any]) -> str:
    """
    The line a board would print for `values` (the simulator's output): integers as
    `%d`, floats to 6 significant digits, a missing value as nothing.
    """
    out: list[str] = []
    for token in pattern.tokens:
        if not isinstance(token, Slot):
            out.append(token)
            continue
        value = values.get(token.name)
        if value is None:
            continue
        if isinstance(value, int | np.integer):
            out.append(str(int(value)))
        else:
            fvalue = float(value)
            out.append(f"{fvalue:.6g}" if math.isfinite(fvalue) else str(fvalue))
    return "".join(out)


def printf_line(pattern: Pattern, types: Mapping[str, str]) -> str:
    """
    The C line that prints what `pattern` matches ("Copy as printf", R8.4), e.g.
    `printf("IMU,%lu,%f\\r\\n", ms, ax);`: unsigned integers as `%lu`, signed as `%ld`,
    numbers as `%f`. `%`, `"` and `\\` in the fixed text are escaped.
    """
    fmt: list[str] = []
    for token in pattern.tokens:
        if isinstance(token, Slot):
            code = STRUCT_TYPE_MAP.get(types.get(token.name, "f32"), ("f",))[0]
            fmt.append("%f" if code in "fd" else "%lu" if code.isupper() else "%ld")
        else:
            fmt.append(token.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%"))
    args = "".join(f", {name}" for name in pattern.slots)
    return f'printf("{"".join(fmt)}\\r\\n"{args});'


def pattern_of(stream: StreamConfig | Mapping[str, Any]) -> str | None:
    """A stream's `frame.pattern`: set means it is a text stream."""
    frame = stream.get("frame")
    pattern = frame.get("pattern") if isinstance(frame, Mapping) else None
    return pattern if isinstance(pattern, str) else None


def default_time_field(stream: StreamConfig | Mapping[str, Any]) -> str:
    """
    The X axis of a stream whose `time` block names no field: `loop_cntr`, except for a
    text stream without a slot of that name, which counts lines (`_line`).
    """
    if pattern_of(stream) is None:
        return LOOP_CNTR_NAME
    frame = stream.get("frame")
    fields = frame.get("fields", []) if isinstance(frame, Mapping) else []
    names = {f.get("name") for f in fields if isinstance(f, Mapping)}
    return LOOP_CNTR_NAME if LOOP_CNTR_NAME in names else LINE_FIELD


def text_dtype(fields: Sequence[Mapping[str, Any]]) -> np.dtype:
    """A text stream's records: each field in native byte order, plus `_line: u32`."""
    spec = [(str(f["name"]), np.dtype(STRUCT_TYPE_MAP[f["type"]][2])) for f in fields]
    return np.dtype([*spec, (LINE_FIELD, np.uint32)])


Converter = Callable[[str], float | int]
"""The slot's text in, the field's value out; raises ValueError."""


def _to_float(text: str) -> float:
    return float(text) if text else math.nan


def _int_converter(dtype: np.dtype) -> Converter:
    info = np.iinfo(dtype)
    lo, hi = int(info.min), int(info.max)

    def convert(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            number = float(text)  # "" and junk raise ValueError, the caller counts it
            if not number.is_integer():  # nan, inf, 1.5
                raise ValueError(text) from None
            value = int(number)
        if not lo <= value <= hi:
            raise ValueError(text)
        return value

    return convert


@dataclass
class _TextStream:
    key: str
    pattern: Pattern
    dtype: np.dtype
    converters: list[Converter]
    counter_index: int | None
    """Position of the time field among the slots, when it is an integer slot (tracked for
    counter gaps and resets like binary `loop_cntr`)."""
    counter_step: float = 1.0
    lines: int = 0
    last_counter: int | None = None
    last_line: str = ""
    rows: list[tuple[Any, ...]] = field(default_factory=list)


class TextLineDecoder:
    """
    The `LinkDecoder` for text profiles: splits lines, matches each against the streams'
    patterns in config order (the first full match wins) and converts the slots.
    """

    def __init__(self) -> None:
        self.stats = LinkStats()
        self.replies: deque[str] = deque(maxlen=REPLIES_KEPT)
        """The newest unmatched lines, oldest first; the n-th is unmatched line number
        `stats.lines_unmatched - len(replies) + n`."""
        self._streams: list[_TextStream] = []
        self._buf = bytearray()
        self._discarding = False  # inside an overlong line: dropping bytes until its `\n`

    def configure(self, streams: dict[str, StreamConfig]) -> None:
        """
        Streams without a pattern (a binary stream, e.g. while switching profiles) are
        left out; an invalid pattern raises ValueError (validation reports it first).
        """
        built: list[_TextStream] = []
        for key, cfg in streams.items():
            text = pattern_of(cfg)
            if text is None:
                continue
            pattern = parse_pattern(text)
            fields = list(cfg["frame"]["fields"])
            names = [str(f["name"]) for f in fields]
            if list(pattern.slots) != names:
                raise ValueError(
                    f"stream '{key}': pattern slots {', '.join(pattern.slots)} don't match "
                    f"the fields {', '.join(names)}"
                )
            dtype = text_dtype(fields)
            converters = [
                _to_float if dtype[name].kind == "f" else _int_converter(dtype[name])
                for name in names
            ]
            raw_time: Any = cfg.get("time")
            time_cfg: dict[str, Any] = raw_time if isinstance(raw_time, dict) else {}
            time_field = str(time_cfg.get("field", default_time_field(cfg)))
            counter = names.index(time_field) if time_field in names else None
            if counter is not None and dtype[time_field].kind == "f":
                counter = None
            step = float(time_cfg.get("step", 1))
            built.append(_TextStream(key, pattern, dtype, converters, counter, step))
        # Counts continue for streams that stay, as the binary router's tracking does.
        previous = {s.key: s for s in self._streams}
        for stream in built:
            old = previous.get(stream.key)
            if old is not None:
                stream.lines, stream.last_counter = old.lines, old.last_counter
        self._streams = built

    def reset(self) -> None:
        self.stats = LinkStats()
        self.replies.clear()
        self._buf = bytearray()
        self._discarding = False
        for stream in self._streams:
            stream.lines = 0
            stream.last_counter = None

    def feed(self, data: bytes) -> dict[str, np.ndarray]:
        stats = self.stats
        stats.bytes_rx += len(data)
        buf = self._buf
        buf += data
        start = 0
        while True:
            end = buf.find(b"\n", start)
            if end < 0:
                break
            if self._discarding:
                self._discarding = False  # the overlong line ends here; already counted
            elif end - start > MAX_LINE_BYTES:
                stats.lines_rx += 1
                stats.lines_overlong += 1
            else:
                self._line(bytes(buf[start:end]))
            start = end + 1
        del buf[:start]
        if len(buf) > MAX_LINE_BYTES:
            if not self._discarding:
                stats.lines_rx += 1
                stats.lines_overlong += 1
                self._discarding = True
            buf.clear()
        return self._flush()

    def _line(self, raw: bytes) -> None:
        line = raw.decode("ascii", errors="replace").strip()
        if not line:
            return  # a blank line is not an error
        stats = self.stats
        stats.lines_rx += 1
        for stream in self._streams:
            match = stream.pattern.regex.fullmatch(line)
            if match is None:
                continue
            try:
                values = [
                    convert(text)
                    for convert, text in zip(stream.converters, match.groups(), strict=True)
                ]
            except ValueError:
                stats.value_errors += 1
                return
            if stream.counter_index is not None:
                self._track_counter(stream, int(values[stream.counter_index]))
            stream.rows.append((*values, stream.lines))
            stream.last_line = line
            stream.lines = (stream.lines + 1) & 0xFFFFFFFF
            stats.frames_decoded += 1
            return
        stats.lines_unmatched += 1
        stats.last_unmatched = line
        self.replies.append(line)

    def _track_counter(self, stream: _TextStream, value: int) -> None:
        """Gaps and resets of an integer time slot, like the binary router's `loop_cntr`."""
        last, stream.last_counter = stream.last_counter, value
        if last is None:
            return
        step = value - last
        if step <= 0:
            self.stats.counter_resets += 1
        elif step > 1.5 * stream.counter_step:
            self.stats.counter_gaps += 1
            self.stats.counter_missing += max(0, round(step / stream.counter_step) - 1)

    def _flush(self) -> dict[str, np.ndarray]:
        """One structured array per stream for the whole chunk, not one per line."""
        out: dict[str, np.ndarray] = {}
        for stream in self._streams:
            if stream.rows:
                out[stream.key] = np.array(stream.rows, dtype=stream.dtype)
                stream.rows = []
                self.stats.last_lines[stream.key] = stream.last_line
        return out
