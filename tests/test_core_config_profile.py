"""Device profiles (R8.2): the `profile` block, schema 3, and the profile folder."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.config import DEFAULT_CONFIG_PATH, StreamConfigLoader, validate_config
from core.config.migrate import migrate
from core.config.profile import (
    Profile,
    list_profiles,
    new_profile_document,
    profile_filename,
    profile_of,
    read_entry,
)

STREAM: dict[str, Any] = {
    "name": "S",
    "frame": {"stream_id": 1, "fields": [{"name": "loop_cntr", "type": "u32"}]},
    "signals": {},
}


def _write(path: Path, doc: dict[str, Any]) -> Path:
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_the_bundled_file_is_the_diffbot_binary_profile() -> None:
    loader = StreamConfigLoader(DEFAULT_CONFIG_PATH)
    assert loader.profile == Profile("diffbot", "binary", 115200)
    assert not loader.migrated


def test_a_schema_2_file_is_a_binary_profile_named_after_the_file(tmp_path: Path) -> None:
    path = _write(tmp_path / "esc-2.json", {"schema_version": 2, "streams": {"s": STREAM}})

    loader = StreamConfigLoader(path)

    assert loader.profile == Profile("esc-2", "binary", None)
    assert loader.migrated and loader.data["schema_version"] == 3
    assert "profile" not in loader.data  # nothing is added: a save writes what was read
    assert migrate(loader.data) == (loader.data, [])


def test_profile_defaults_fill_what_the_block_leaves_out() -> None:
    assert profile_of({"profile": {"baud": 9600}}, "x/robot.json") == Profile(
        "robot", "binary", 9600
    )
    assert profile_of({"profile": {"name": " ", "baud": True}}, "a.json") == Profile("a")


def test_an_unknown_format_makes_the_file_unusable(tmp_path: Path) -> None:
    doc = {"schema_version": 3, "profile": {"format": "morse"}, "streams": {"s": STREAM}}
    problems = validate_config(doc)
    assert any(p.fatal and "profile.format 'morse'" in p.message for p in problems)
    with pytest.raises(ValueError, match="morse"):
        StreamConfigLoader(_write(tmp_path / "p.json", doc))


def test_profile_block_mistakes_are_warnings() -> None:
    doc = {
        "schema_version": 3,
        "profile": {"name": 7, "baud": -1, "colour": "red"},
        "streams": {"s": STREAM},
    }
    messages = [p.message for p in validate_config(doc) if p.severity == "warning"]
    assert "profile.name must be a non-empty string" in messages
    assert "profile.baud must be a positive integer, got -1" in messages
    assert "unknown profile key(s) colour" in messages


def test_opening_another_profile_and_falling_back(tmp_path: Path) -> None:
    first = _write(tmp_path / "a.json", {"schema_version": 3, "streams": {"s": STREAM}})
    second = _write(
        tmp_path / "b.json",
        {"schema_version": 3, "profile": {"name": "Bee", "baud": 9600}, "streams": {"t": STREAM}},
    )
    broken = tmp_path / "broken.json"
    broken.write_text("{nope", encoding="utf-8")
    loader = StreamConfigLoader(first)

    loader.open(second)
    assert loader.path == second and loader.profile.name == "Bee"
    assert list(loader.list_streams()) == ["t"]

    with pytest.raises(ValueError):
        loader.open(broken)
    assert loader.path == second and list(loader.list_streams()) == ["t"]  # unchanged
    with pytest.raises(FileNotFoundError):
        loader.open(tmp_path / "missing.json")
    assert loader.path == second


def test_listing_profiles_reads_names_and_skips_non_profiles(tmp_path: Path) -> None:
    folder = tmp_path / "profiles"
    folder.mkdir()
    _write(folder / "robot-1.json", {"profile": {"name": "robot-1"}, "streams": {}})
    _write(folder / "zeta.json", {"streams": {}})
    _write(folder / "notes.json", {"hello": "world"})  # not a profile
    (folder / "broken.json").write_text("{", encoding="utf-8")
    elsewhere = _write(tmp_path / "Alpha.json", {"profile": {"format": "binary"}, "streams": {}})

    entries = list_profiles(folder, [elsewhere, folder / "zeta.json", tmp_path / "gone.json"])

    assert [(e.name, e.format) for e in entries] == [
        ("Alpha", "binary"),
        ("robot-1", "binary"),
        ("zeta", "binary"),
    ]
    assert read_entry(folder / "notes.json") is None
    assert list_profiles(tmp_path / "no-such-folder") == []


def test_a_new_profile_is_empty_or_a_copy(tmp_path: Path) -> None:
    empty = new_profile_document("esc-3", "binary", 57600)
    assert empty == {
        "schema_version": 3,
        "profile": {"name": "esc-3", "format": "binary", "baud": 57600},
        "streams": {},
    }
    source = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    copy = new_profile_document("robot-2", "binary", None, copy_of=source)
    assert copy["profile"] == {"name": "robot-2", "format": "binary"}
    assert copy["streams"] == source["streams"] and copy["panels"] == source["panels"]
    assert copy["streams"] is not source["streams"]
    assert [p for p in validate_config(copy) if p.severity == "error"] == []


def test_profile_file_names_are_safe_and_free(tmp_path: Path) -> None:
    assert profile_filename("robot 1/α", tmp_path) == tmp_path / "robot-1.json"
    (tmp_path / "esc.json").write_text("{}", encoding="utf-8")
    assert profile_filename("esc", tmp_path) == tmp_path / "esc-2.json"
    assert profile_filename("..", tmp_path) == tmp_path / "profile.json"
