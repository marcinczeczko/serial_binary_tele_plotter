"""
The `streams.json` document (R5.1): load → migrate → validate → save.

`validate_config()` is the single source of truth for what a usable document is. The loader
uses it to keep broken streams, commands and panels out of the UI; the Configuration tab
uses it (through `save_document`) to refuse writing a file the app couldn't load.

Stream definitions stay plain JSON objects typed as `StreamConfig`: the editor round-trips
them losslessly, unknown keys and key order included (C4). Commands and panels are parsed
into dataclasses (`CommandDef`, `PanelDef`) for the code that uses them.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, cast

from core.config.controls import PanelDef, parse_commands, parse_panels
from core.config.migrate import SCHEMA_VERSION, SchemaError, migrate, schema_version
from core.config.profile import Profile, profile_of, profile_problems
from core.config.streams import (
    ConfigProblem,
    same_pattern_problems,
    shared_id_problems,
    validate_stream,
)
from core.protocol.commands import CommandDef
from core.types import StreamConfig

# The streams.json shipped next to the application code (not the current working directory).
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "streams.json"

TOP_LEVEL_KEYS = ("schema_version", "profile", "commands", "panels", "streams")


class InvalidConfigError(ValueError):
    """Saving was refused: the document has errors."""

    def __init__(self, problems: list[ConfigProblem]) -> None:
        self.problems = problems
        super().__init__("\n".join(str(p) for p in problems))


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
    """Every problem in a parsed document (an older schema is checked as migrated)."""
    if not isinstance(data, dict):
        return [ConfigProblem("error", None, "the document must be a JSON object")]
    try:
        doc, _ = migrate(data)
    except SchemaError as e:
        return [ConfigProblem("error", None, str(e))]
    streams = doc.get("streams")
    if not isinstance(streams, dict):
        return [ConfigProblem("error", None, "missing or invalid 'streams' object")]

    problems: list[ConfigProblem] = []
    unknown = sorted(set(doc) - set(TOP_LEVEL_KEYS))
    if unknown:
        problems.append(
            ConfigProblem("warning", None, f"unknown top-level key(s) {', '.join(unknown)}")
        )
    problems.extend(profile_problems(doc))
    fmt = profile_of(doc, "").format
    for key, stream in streams.items():
        problems.extend(validate_stream(str(key), stream, fmt))
    if fmt == "text":
        problems.extend(same_pattern_problems(streams))
    else:
        problems.extend(shared_id_problems(streams))

    raw_commands = doc.get("commands")
    commands, command_problems = parse_commands(raw_commands)
    declared = set(raw_commands) if isinstance(raw_commands, dict) else set()
    panels, panel_problems = parse_panels(doc.get("panels"), commands, declared)
    problems += command_problems + panel_problems

    raw_panels = doc.get("panels")
    for key, stream in streams.items():
        controls = stream.get("controls") if isinstance(stream, dict) else None
        if not isinstance(controls, str) or controls in panels:
            continue
        exists = isinstance(raw_panels, dict) and controls in raw_panels
        reason = "has errors" if exists else "is not defined"
        problems.append(
            ConfigProblem("warning", str(key), f"controls panel '{controls}' {reason}; not shown")
        )
    return problems


def save_document(path: str | Path, doc: dict[str, Any]) -> list[ConfigProblem]:
    """
    Writes a document the app can load, and returns its warnings. Raises
    InvalidConfigError (nothing written) if it has errors, OSError if writing fails.

    The previous file is kept as `<name>.bak`, and the new one is written to a temporary
    file first, so a failed write never leaves a truncated streams.json.
    """
    problems = validate_config(doc)
    errors = [p for p in problems if p.severity == "error"]
    if errors:
        raise InvalidConfigError(errors)
    path = Path(path)
    if path.exists():
        try:
            shutil.copy(path, path.with_name(path.name + ".bak"))
        except OSError:
            pass  # a missing backup doesn't stop the save
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(doc, f, indent=4)
    os.replace(tmp, path)
    return problems


class StreamConfigLoader:
    """
    Loads `streams.json` and exposes what's usable: valid streams, commands and panels.

    Older schema versions are migrated in memory (`migration_notes`); the file isn't
    touched until the user saves from the Configuration tab. Every problem (errors and
    warnings) is in `problems` so the UI can report it.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._streams: dict[str, StreamConfig] = {}
        self.data: dict[str, Any] = {}
        self.problems: list[ConfigProblem] = []
        self.commands: dict[str, CommandDef] = {}
        self.panels: dict[str, PanelDef] = {}
        self.source_version = SCHEMA_VERSION
        self.migration_notes: list[str] = []
        self.profile = Profile(self.path.stem)

        if not self.path.exists():
            raise FileNotFoundError(f"Required configuration file not found: {self.path.resolve()}")

        self.load()

    @property
    def migrated(self) -> bool:
        return self.source_version < SCHEMA_VERSION

    def load(self) -> None:
        """Loads, migrates, validates and filters the JSON configuration file."""
        try:
            with self.path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {self.path}: {e}") from e
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid {self.path.name}: the document must be a JSON object")
        try:
            self.source_version = schema_version(raw)
            self.data, self.migration_notes = migrate(raw)
        except SchemaError as e:
            raise ValueError(f"Invalid {self.path.name}: {e}") from e

        self.problems = validate_config(self.data)
        fatal = [p for p in self.problems if p.fatal]
        if fatal:
            raise ValueError(f"Invalid {self.path.name}: {fatal[0].message}")

        self.profile = profile_of(self.data, self.path)
        broken = {p.stream for p in self.problems if p.severity == "error"}
        # Shallow copies: `self.data` stays as loaded, because the config editor round-trips it.
        self._streams = {
            key: cast(StreamConfig, dict(stream))
            for key, stream in self.data["streams"].items()
            if key not in broken
        }
        raw_commands = self.data.get("commands")
        self.commands, _ = parse_commands(raw_commands)
        declared = set(raw_commands) if isinstance(raw_commands, dict) else set()
        self.panels, _ = parse_panels(self.data.get("panels"), self.commands, declared)

    def open(self, path: str | Path) -> None:
        """
        Switches to another profile file. If it can't be loaded, the loader stays on the
        file it had (and raises the error: FileNotFoundError or ValueError).
        """
        previous = self.path
        self.path = Path(path)
        try:
            if not self.path.exists():
                raise FileNotFoundError(f"Profile file not found: {self.path.resolve()}")
            self.load()
        except OSError, ValueError:
            self.path = previous
            self.load()
            raise

    def list_streams(self) -> dict[str, StreamConfig]:
        """Returns all valid stream definitions."""
        return self._streams

    def get_stream(self, stream_id: str) -> StreamConfig:
        """Returns configuration for a specific stream."""
        try:
            return self._streams[stream_id]
        except KeyError as e:
            raise KeyError(f"Stream '{stream_id}' not found in streams.json") from e

    def panel_for(self, stream_key: str | None) -> PanelDef | None:
        """The control panel a stream shows, if it names a valid one."""
        stream = self._streams.get(stream_key or "")
        controls = stream.get("controls") if stream else None
        return self.panels.get(controls) if isinstance(controls, str) else None
