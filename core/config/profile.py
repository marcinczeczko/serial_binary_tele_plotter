"""
Device profiles (R8.2, ADR-0010).

A profile is one config file (schema 3): the streams, commands and panels of one device,
plus a top-level `profile` block saying what the device sends:

    "profile": {"name": "robot-1", "format": "binary", "baud": 115200}

- `name`: what the profile menu shows. Default: the file's name without `.json`.
- `format`: the wire format, a `LinkDecoder` name (`core.protocol.link`). Default: binary.
- `baud`: the baud rate to connect at, until the user picks another for this profile.

A file without the block is a binary profile named after the file, so every schema 2
`streams.json` is one. Profiles live one per file in a folder the user can open, commit
next to the firmware, or share; the menu also offers files opened from elsewhere.

Pure Python, no Qt.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.config.migrate import SCHEMA_VERSION
from core.config.streams import ConfigProblem
from core.protocol.link import BINARY, LINK_FORMATS

PROFILE_KEYS = ("name", "format", "baud")
FORMAT_LABELS = {"binary": "Binary frames", "text": "Text lines"}


@dataclass(frozen=True)
class Profile:
    name: str
    format: str = BINARY
    baud: int | None = None


@dataclass(frozen=True)
class ProfileEntry:
    """A profile file, as the menu lists it (read without validating the whole file)."""

    path: Path
    name: str
    format: str


def profile_of(doc: dict[str, Any], path: str | Path) -> Profile:
    """The document's profile, with the defaults for anything it doesn't say."""
    block = doc.get("profile")
    block = block if isinstance(block, dict) else {}
    name = block.get("name")
    fmt = block.get("format")
    baud = block.get("baud")
    return Profile(
        name=name if isinstance(name, str) and name.strip() else Path(path).stem,
        format=fmt if isinstance(fmt, str) else BINARY,
        baud=baud if isinstance(baud, int) and not isinstance(baud, bool) and baud > 0 else None,
    )


def profile_problems(doc: dict[str, Any]) -> list[ConfigProblem]:
    """Problems of the `profile` block. An unknown format makes the file unusable."""
    if "profile" not in doc:
        return []
    block = doc["profile"]
    if not isinstance(block, dict):
        return [ConfigProblem("error", None, "'profile' must be an object")]
    problems: list[ConfigProblem] = []
    if "name" in block and (not isinstance(block["name"], str) or not block["name"].strip()):
        problems.append(ConfigProblem("warning", None, "profile.name must be a non-empty string"))
    fmt = block.get("format", BINARY)
    if fmt not in LINK_FORMATS:
        problems.append(
            ConfigProblem(
                "error",
                None,
                f"profile.format {fmt!r} is not supported (known: {', '.join(LINK_FORMATS)})",
            )
        )
    baud = block.get("baud")
    if "baud" in block and (not isinstance(baud, int) or isinstance(baud, bool) or baud <= 0):
        problems.append(
            ConfigProblem("warning", None, f"profile.baud must be a positive integer, got {baud!r}")
        )
    unknown = sorted(set(block) - set(PROFILE_KEYS))
    if unknown:
        problems.append(
            ConfigProblem("warning", None, f"unknown profile key(s) {', '.join(unknown)}")
        )
    return problems


def read_entry(path: str | Path) -> ProfileEntry | None:
    """A file's name and format for the menu; None if it isn't a JSON object."""
    path = Path(path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("streams"), dict):
        return None
    profile = profile_of(doc, path)
    return ProfileEntry(path, profile.name, profile.format)


def list_profiles(
    folder: str | Path | None, extra: Iterable[str | Path] = ()
) -> list[ProfileEntry]:
    """
    The profiles in `folder` (`*.json`) and the `extra` files (the bundled one, recently
    opened ones), each once, by name. Files that aren't profiles are left out.
    """
    paths: list[Path] = []
    if folder is not None and Path(folder).is_dir():
        paths += sorted(Path(folder).glob("*.json"))
    paths += [Path(p) for p in extra]
    seen: set[Path] = set()
    entries: list[ProfileEntry] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        entry = read_entry(resolved)
        if entry is not None:
            entries.append(entry)
    return sorted(entries, key=lambda e: (e.name.lower(), str(e.path)))


def new_profile_document(
    name: str, fmt: str, baud: int | None, copy_of: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    A new profile: empty, or a copy of another document (its streams, commands and
    panels) under the new name, format and baud.
    """
    block: dict[str, Any] = {"name": name, "format": fmt}
    if baud is not None:
        block["baud"] = baud
    doc: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "profile": block}
    if copy_of is not None:
        for key, value in copy_of.items():
            if key not in ("schema_version", "profile"):
                doc[key] = copy.deepcopy(value)
    doc.setdefault("streams", {})
    return doc


def profile_filename(name: str, folder: str | Path) -> Path:
    """A free file name in `folder` for a profile called `name` (`robot-1.json`, …)."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-.") or "profile"
    folder = Path(folder)
    path, n = folder / f"{stem}.json", 2
    while path.exists():
        path, n = folder / f"{stem}-{n}.json", n + 1
    return path
