"""
C structs in and out (R7.2, R7.3).

The firmware already describes each frame as a C struct, so the editor can start a stream
from one: `parse_c_struct()` reads the fields, `stream_from_struct()` makes a stream whose
signals are labelled with the field names, and `replace_fields()` updates a stream when
the struct changes. `to_c_struct()` goes the other way, so firmware and config can't drift.

What's read:
- fixed-width integers (`uint8_t` … `int64_t`), `float`, `double`, `bool`, and
  `char`/`short`/`int` with their usual 32-bit MCU sizes;
- several names on one line, one-dimensional arrays (one field per element, `name_0` …);
- `__attribute__((packed))` or `#pragma pack(1)`; without them, the padding a 32-bit
  compiler inserts becomes `_pad` fields, so the layout still matches the wire;
- `#define`s: an ID (a name containing "ID"), and array sizes.

Anything else (nested structs, bit-fields, pointers, `long`) is listed in `problems` and
left out, never guessed. Only the bare member lines may be pasted, too.

Pure Python, no Qt.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from core.config.draft import LINE_WIDTH, PALETTE, type_size
from core.config.streams import MAX_PAYLOAD_BYTES
from core.protocol.constants import LOOP_CNTR_NAME

# C spelling -> frame type. Longest first, so "unsigned short int" wins over "short".
C_TYPES: dict[str, str] = {
    "uint8_t": "u8",
    "int8_t": "i8",
    "uint16_t": "u16",
    "int16_t": "i16",
    "uint32_t": "u32",
    "int32_t": "i32",
    "uint64_t": "u64",
    "int64_t": "i64",
    "float": "f32",
    "double": "f64",
    "bool": "u8",
    "_Bool": "u8",
    "unsigned char": "u8",
    "signed char": "i8",
    "char": "i8",
    "unsigned short int": "u16",
    "unsigned short": "u16",
    "signed short int": "i16",
    "signed short": "i16",
    "short int": "i16",
    "short": "i16",
    "unsigned int": "u32",
    "unsigned": "u32",
    "signed int": "i32",
    "signed": "i32",
    "int": "i32",
}
TO_C: dict[str, str] = {
    "u8": "uint8_t",
    "i8": "int8_t",
    "u16": "uint16_t",
    "i16": "int16_t",
    "u32": "uint32_t",
    "i32": "int32_t",
    "u64": "uint64_t",
    "i64": "int64_t",
    "f32": "float",
    "f64": "double",
}
PAD_PREFIX = "_pad"
_QUALIFIERS = re.compile(r"\b(?:const|volatile|static|register)\b")
_ATTRIBUTE = re.compile(r"__attribute__\s*\(\((?:[^()]|\([^()]*\))*\)\)")
_DEFINE = re.compile(
    r"^[ \t]*#[ \t]*define[ \t]+(\w+)[ \t]+\(?[ \t]*(0[xX][0-9a-fA-F]+|\d+)[uUlL]*[ \t]*\)?[ \t]*$",
    re.MULTILINE,
)
_DECLARATOR = re.compile(r"([A-Za-z_]\w*)\s*(?:\[\s*([A-Za-z_]\w*|0[xX][0-9a-fA-F]+|\d+)\s*\])?")


@dataclass
class ParsedStruct:
    """The fields read from C source, in order, and what couldn't be read."""

    name: str | None = None
    fields: list[tuple[str, str]] = field(default_factory=list)  # (name, frame type)
    packed: bool = False
    stream_id: int | None = None
    id_source: str | None = None  # the #define the ID came from
    problems: list[str] = field(default_factory=list)  # left out; fix by hand
    notes: list[str] = field(default_factory=list)  # read, but worth knowing

    @property
    def size(self) -> int:
        return sum(type_size(t) for _, t in self.fields)


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", text)


def _int(text: str) -> int | None:
    try:
        return int(text, 0)
    except ValueError:
        return None


def _defines(text: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for m in _DEFINE.finditer(text):
        value = _int(m.group(2))
        if value is not None:
            found[m.group(1)] = value
    return found


def _struct_parts(text: str) -> tuple[str, str, str] | None:
    """(head, body, tail) of the first `struct … { … } …;`, braces matched."""
    m = re.search(r"\bstruct\b([^{;]*)\{", text)
    if m is None:
        return None
    depth, i = 1, m.end()
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    if depth:
        return None
    end = text.find(";", i)
    tail = text[i : end if end >= 0 else len(text)]
    return m.group(1), text[m.end() : i - 1], tail


def _last_identifier(text: str) -> str | None:
    names = re.findall(r"[A-Za-z_]\w*", _ATTRIBUTE.sub(" ", text))
    names = [n for n in names if n not in ("typedef", "struct")]
    return names[-1] if names else None


_TYPE_PATTERNS = [
    (spelling, re.compile(r"\s+".join(map(re.escape, spelling.split())) + r"\b"))
    for spelling in sorted(C_TYPES, key=len, reverse=True)
]


def _split_type(decl: str) -> tuple[str | None, str]:
    for spelling, pattern in _TYPE_PATTERNS:
        m = pattern.match(decl)
        if m is not None:
            return spelling, decl[m.end() :].strip()
    return None, decl


def parse_c_struct(text: str) -> ParsedStruct:
    """Reads a struct (or bare member lines) the way a 32-bit MCU compiler lays it out."""
    parsed = ParsedStruct()
    source = _strip_comments(text)
    defines = _defines(source)
    ids = [(k, v) for k, v in defines.items() if "ID" in k.upper() and 0 <= v <= 255]
    if len(ids) == 1:
        parsed.id_source, parsed.stream_id = ids[0]
    parsed.packed = bool(re.search(r"#\s*pragma\s+pack\s*\(\s*(?:push\s*,\s*)?1\s*\)", source))

    body = re.sub(r"^[ \t]*#.*$", "", source, flags=re.MULTILINE)
    parts = _struct_parts(body)
    if parts is None:
        parsed.packed = True  # bare member lines: the frame itself is packed
    else:
        head, body, tail = parts
        parsed.packed = parsed.packed or "packed" in head or "packed" in tail
        parsed.name = _last_identifier(tail) or _last_identifier(head)

    while True:  # nested struct/union bodies can't be read; drop them, reported below
        inner = re.sub(r"\{[^{}]*\}", " <nested> ", body)
        if inner == body:
            break
        body = inner

    fields: list[tuple[str, str]] = []
    for raw in body.split(";"):
        decl = " ".join(_QUALIFIERS.sub(" ", _ATTRIBUTE.sub(" ", raw)).split())
        if not decl:
            continue
        if "<nested>" in decl or re.match(r"(struct|union|enum)\b", decl):
            parsed.problems.append(
                f"'{decl.replace('<nested>', '{ … }')}': nested structs, unions and enums are "
                "not read; add their fields by hand"
            )
            continue
        spelling, rest = _split_type(decl)
        if spelling is None:
            word = decl.split()[0]
            why = (
                "its size depends on the compiler; use int32_t or int64_t"
                if word == "long"
                else f"unknown type '{word}'"
            )
            parsed.problems.append(f"'{decl}': {why}")
            continue
        for declarator in (d.strip() for d in rest.split(",")):
            if "*" in declarator:
                parsed.problems.append(f"'{declarator}': pointers can't be sent")
                continue
            if ":" in declarator:
                parsed.problems.append(
                    f"'{declarator}': bit-fields are not read; send the whole integer instead"
                )
                continue
            m = _DECLARATOR.fullmatch(declarator)
            if m is None:
                parsed.problems.append(f"'{spelling} {declarator}': not understood")
                continue
            name, count_text = m.group(1), m.group(2)
            ftype = C_TYPES[spelling]
            if count_text is None:
                fields.append((name, ftype))
                continue
            count = _int(count_text) if count_text[0].isdigit() else defines.get(count_text)
            if count is None or count <= 0:
                parsed.problems.append(f"'{declarator}': unknown array size '{count_text}'")
                continue
            fields.extend((f"{name}_{i}", ftype) for i in range(count))

    parsed.fields = fields if parsed.packed else _with_padding(fields, parsed)
    names = [n for n, _ in parsed.fields]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        parsed.problems.append(f"duplicate field name(s): {', '.join(dupes)}")
    if parsed.fields and LOOP_CNTR_NAME not in names:
        parsed.problems.append(
            f"no '{LOOP_CNTR_NAME}' field: every frame needs one (u32, first) to count lost frames"
        )
    if parsed.size > MAX_PAYLOAD_BYTES:
        parsed.problems.append(
            f"{parsed.size} B is more than the {MAX_PAYLOAD_BYTES} B a frame can carry"
        )
    return parsed


def _with_padding(fields: list[tuple[str, str]], parsed: ParsedStruct) -> list[tuple[str, str]]:
    """Natural alignment (each type on a multiple of its size), as the MCU compiler does."""
    out: list[tuple[str, str]] = []
    offset, widest, pads = 0, 1, 0

    def pad_to(boundary: int) -> None:
        nonlocal offset, pads
        while offset % boundary:
            out.append((f"{PAD_PREFIX}{offset}", "u8"))
            offset += 1
            pads += 1

    for name, ftype in fields:
        size = type_size(ftype)
        widest = max(widest, size)
        pad_to(size)
        out.append((name, ftype))
        offset += size
    if fields:
        pad_to(widest)
    if pads:
        parsed.notes.append(
            f"not packed: {pads} padding byte(s) the compiler inserts are kept as "
            f"'{PAD_PREFIX}' fields; __attribute__((packed)) avoids them"
        )
    return out


def stream_from_struct(
    parsed: ParsedStruct,
    name: str,
    stream_id: int,
    endianness: str = "little",
    scale_s: float | None = None,
) -> dict[str, Any]:
    """A new stream: every field plotted (except the counter and padding), label = name."""
    stream: dict[str, Any] = {
        "name": name,
        "frame": {
            "stream_id": stream_id,
            "endianness": endianness,
            "fields": [{"name": n, "type": t} for n, t in parsed.fields],
        },
    }
    if scale_s is not None:
        stream["time"] = {"field": LOOP_CNTR_NAME, "scale_s": scale_s}
    stream["signals"] = {}
    for n, _ in parsed.fields:
        if _plottable(n):
            stream["signals"][n] = _new_signal(n, PALETTE[len(stream["signals"]) % len(PALETTE)])
    return stream


def replace_fields(stream: dict[str, Any], parsed: ParsedStruct) -> tuple[dict[str, Any], str]:
    """
    The stream with the struct's fields (the firmware changed). Signals of fields that
    keep their name keep their label, color and lane; new fields get a signal; signals
    of fields that are gone are removed. Returns (stream, a one-line summary).
    """
    out = copy.deepcopy(stream)
    frame = out.setdefault("frame", {})
    old_fields = {f.get("name"): f for f in frame.get("fields", []) if isinstance(f, dict)}
    new_names = [n for n, _ in parsed.fields]
    fields: list[dict[str, Any]] = []
    for n, t in parsed.fields:
        f = old_fields.get(n, {"name": n})
        f["type"] = t
        fields.append(f)
    frame["fields"] = fields

    raw_signals = out.get("signals")
    signals: dict[str, Any] = raw_signals if isinstance(raw_signals, dict) else {}
    removed = [
        k for k, s in signals.items() if isinstance(s, dict) and s.get("field") not in new_names
    ]
    for k in removed:
        del signals[k]
    sim = out.get("sim")
    if isinstance(sim, dict) and isinstance(sim.get("fields"), dict):
        for gone in [k for k in sim["fields"] if k not in new_names]:
            del sim["fields"][gone]
    added = [n for n in new_names if n not in old_fields]
    used = {str(s.get("color", "")).lower() for s in signals.values() if isinstance(s, dict)}
    free = [c for c in PALETTE if c.lower() not in used] or list(PALETTE)
    for i, n in enumerate(a for a in added if _plottable(a)):
        key = n if n not in signals else f"{n}_new"
        signals[key] = _new_signal(n, free[i % len(free)])
    out["signals"] = signals
    kept = len(new_names) - len(added)
    summary = f"{kept} field(s) kept, {len(added)} added, {len(old_fields) - kept} removed"
    return out, summary


def _plottable(name: str) -> bool:
    return name != LOOP_CNTR_NAME and not name.startswith(PAD_PREFIX)


def _new_signal(field_name: str, color: str) -> dict[str, Any]:
    return {
        "label": field_name,
        "field": field_name,
        "color": color,
        "line": {"style": "solid", "width": LINE_WIDTH},
        "visible": True,
    }


def c_identifier(text: str) -> str:
    ident = re.sub(r"\W", "_", text)
    return ident if ident and not ident[0].isdigit() else f"_{ident}"


def to_c_struct(key: str, stream: dict[str, Any]) -> str:
    """The stream's frame as a packed C struct, with its ID and a size check."""
    frame = stream.get("frame", {})
    fields = [f for f in frame.get("fields", []) if isinstance(f, dict)]
    ident = c_identifier(key)
    size = sum(type_size(f.get("type")) for f in fields)
    stream_id = frame.get("stream_id", 0)
    lines = [
        f'// {stream.get("name", key)} (streams.json: "{key}"), {size} bytes.',
    ]
    if frame.get("endianness", "little") == "big":
        lines.append("// Big-endian on the wire: send each field most significant byte first.")
    lines += [
        f"#define {ident.upper()}_STREAM_ID 0x{stream_id:02X}"
        if isinstance(stream_id, int)
        else f"#define {ident.upper()}_STREAM_ID {stream_id}",
        "",
        "typedef struct __attribute__((packed)) {",
    ]
    width = max((len(TO_C.get(str(f.get("type")), "?")) for f in fields), default=0)
    for f in fields:
        ctype = TO_C.get(str(f.get("type")), f"/* unknown {f.get('type')} */ uint8_t")
        lines.append(f"    {ctype:<{width}} {c_identifier(str(f.get('name', '')))};")
    lines += [
        f"}} {ident}_t;",
        "",
        f'_Static_assert(sizeof({ident}_t) == {size}, "{ident}_t must match streams.json");',
        "",
    ]
    return "\n".join(lines)
