"""Config-defined commands (R5.2), schema migration and the document model (R5.1). No Qt."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import pytest

from core.config import (
    DEFAULT_CONFIG_PATH,
    InvalidConfigError,
    SchemaError,
    StreamConfigLoader,
    migrate,
    parse_commands,
    parse_panels,
    save_document,
    validate_config,
)
from core.protocol.commands import (
    CommandDef,
    CommandError,
    CommandField,
    decode_command,
    encode_command,
    resolve_values,
)
from core.protocol.crc import calculate_crc8

V1_FIXTURE = Path(__file__).parent / "fixtures" / "streams_v1.json"


def _framed(packet_id: int, payload: bytes) -> bytes:
    header = bytes([0xAA, 0x55, packet_id, len(payload)])
    return header + bytes([calculate_crc8(header)]) + payload + bytes([calculate_crc8(payload)])


def _bundled() -> StreamConfigLoader:
    return StreamConfigLoader(DEFAULT_CONFIG_PATH)


# --- wire compatibility of the bundled commands (firmware-visible) ---

GAINS = (0.1, -0.02, 26.5, 8.0, 5.0, 1.0, 0.2, -0.3)  # kp, ki, k1, k2, k3, k_aw, alpha, rps
NAMES = ("kp", "ki", "k1", "k2", "k3", "k_aw", "alpha", "rps")


def test_bundled_pid_single_is_byte_identical_to_the_firmware_layout() -> None:
    loader = _bundled()
    panel = loader.panels["diffbot_pid"]
    params = {"Right": {**dict(zip(NAMES, GAINS, strict=True)), "use_ramp": 1, "use_pi": 0}}
    button = panel.buttons[1]  # "Update Right PID"
    assert (button.label, button.column) == ("Update Right PID", "Right")

    values = resolve_values(loader.commands["pid_single"], params, "Right", button.values)
    packet = encode_command(loader.commands["pid_single"], values)

    # The layout firmware expects (it was hard-coded before R5.2): ID 0x10,
    # motor_id, kp, ki, k1, k2, k3, k_aw, alpha, rps, use_ramp, use_pi.
    assert packet == _framed(0x10, struct.pack("<BffffffffBB", 1, *GAINS, 1, 0))


def test_bundled_pid_both_is_byte_identical_to_the_firmware_layout() -> None:
    loader = _bundled()
    left = {**dict(zip(NAMES, GAINS, strict=True)), "use_ramp": 0, "use_pi": 1}
    right = {**{n: v * 2 for n, v in zip(NAMES, GAINS, strict=True)}, "use_ramp": 1, "use_pi": 1}
    command = loader.commands["pid_both"]

    packet = encode_command(command, resolve_values(command, {"Left": left, "Right": right}))

    both = [*GAINS, 0, 1, *(v * 2 for v in GAINS), 1, 1]
    assert packet == _framed(0x11, struct.pack("<ffffffffBBffffffffBB", *both))


# --- encoding ---

CMD = CommandDef(
    "cmd",
    "Test",
    0x30,
    (
        CommandField("id", "u8"),
        CommandField("gain", "f32", param="gain"),
        CommandField("mode", "i16", value=3),
        CommandField("other", "f32", param="gain", column="B"),
    ),
)


def test_resolve_values_prefers_button_values_then_constants_then_params() -> None:
    params = {"A": {"gain": 1.5}, "B": {"gain": -2.0}}
    values = resolve_values(CMD, params, "A", {"id": 7})
    assert values == {"id": 7, "gain": 1.5, "mode": 3, "other": -2.0}
    assert resolve_values(CMD, params, "A", {"id": 1, "mode": 9})["mode"] == 9
    with pytest.raises(CommandError, match="no value given"):
        resolve_values(CMD, params, "A")  # nothing gives `id`
    with pytest.raises(CommandError, match="no column"):
        resolve_values(CMD, params, None, {"id": 1})


def test_encode_refuses_values_that_do_not_fit_instead_of_wrapping() -> None:
    ok = {"id": 255, "gain": 0.5, "mode": -3, "other": 0.0}
    assert len(encode_command(CMD, ok)) == 5 + 1 + 4 + 2 + 4 + 1
    for bad, match in (
        ({"id": 256}, r"outside u8 \(0\.\.255\)"),
        ({"id": -1}, "outside u8"),
        ({"id": 1.5}, "whole number"),
        ({"gain": 1e39}, "doesn't fit in f32"),
        ({"mode": float("nan")}, "whole number"),
    ):
        with pytest.raises(CommandError, match=match):
            encode_command(CMD, {**ok, **bad})
    with pytest.raises(CommandError, match="no value"):
        encode_command(CMD, {"id": 1})


def test_decode_is_the_inverse_of_encode() -> None:
    values = {"id": 9, "gain": 0.25, "mode": -7, "other": 1.5}
    payload = encode_command(CMD, values)[5:-1]
    assert decode_command(CMD, payload) == values
    assert decode_command(CMD, payload[:-1]) is None  # wrong size
    big = CommandDef("b", "B", 1, (CommandField("x", "u16"),), endianness="big")
    assert encode_command(big, {"x": 0x0102})[5:7] == b"\x01\x02"


# --- migration (schema 1 -> 2 -> 3) ---


def test_schema_1_file_migrates_to_the_bundled_file() -> None:
    raw = json.loads(V1_FIXTURE.read_text(encoding="utf-8"))
    migrated, notes = migrate(raw)

    bundled = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    del bundled["profile"]  # the bundled file names its profile; a v1 file can't
    assert migrated == bundled
    assert notes[-1] == "migrated from schema version 2 to 3 (a binary device profile)"
    assert list(migrated)[:3] == ["schema_version", "commands", "panels"]
    assert list(migrated["streams"]["pid"])[:3] == ["name", "controls", "frame"]  # in place
    assert "panel_type" not in json.dumps(migrated)
    assert notes[0] == "migrated from schema version 1 to 2"
    assert any("'imu_6axis': panel_type 'imu' dropped" in n for n in notes)
    assert "panel_type" in raw["streams"]["pid"]  # the input isn't modified
    assert migrate(migrated) == (migrated, [])  # the current version is left alone


def test_schema_1_migration_keeps_user_commands_and_panels() -> None:
    raw: dict[str, Any] = {
        "commands": {"pid_single": {"packet_id": 99, "fields": []}},
        "streams": {"s": {"name": "S", "panel_type": "pid", "frame": {}}},
    }
    migrated, _ = migrate(raw)
    assert migrated["commands"]["pid_single"]["packet_id"] == 99  # never overwritten
    assert "pid_both" in migrated["commands"] and "diffbot_pid" in migrated["panels"]


@pytest.mark.parametrize(
    ("version", "match"), [(4, "newer version"), ("2", "positive"), (0, "positive")]
)
def test_unreadable_schema_versions_are_refused(version: Any, match: str, tmp_path: Path) -> None:
    with pytest.raises(SchemaError, match=match):
        migrate({"schema_version": version, "streams": {}})
    path = tmp_path / "streams.json"
    path.write_text(json.dumps({"schema_version": version, "streams": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid streams.json"):
        StreamConfigLoader(path)
    assert [p.fatal for p in validate_config({"schema_version": version, "streams": {}})] == [True]


def test_loader_migrates_in_memory_and_leaves_the_file(tmp_path: Path) -> None:
    path = tmp_path / "streams.json"
    text = V1_FIXTURE.read_text(encoding="utf-8")
    path.write_text(text, encoding="utf-8")

    loader = StreamConfigLoader(path)

    assert loader.migrated and loader.source_version == 1 and len(loader.migration_notes) == 5
    assert [str(p) for p in loader.problems] == []
    assert loader.panel_for("pid") is loader.panels["diffbot_pid"]
    assert loader.panel_for("imu_6axis") is None
    assert path.read_text(encoding="utf-8") == text


# --- validation of commands and panels ---


def _panel(**overrides: Any) -> dict[str, Any]:
    panel: dict[str, Any] = {
        "title": "T",
        "columns": ["A", "B"],
        "parameters": {"gain": {"label": "Gain", "default": 1.0}},
        "buttons": [{"label": "Send A", "command": "cmd", "column": "A", "values": {"id": 1}}],
    }
    panel.update(overrides)
    return panel


COMMANDS: dict[str, Any] = {
    "cmd": {
        "packet_id": 48,
        "fields": [
            {"name": "id", "type": "u8"},
            {"name": "gain", "type": "f32", "param": "gain"},
        ],
    }
}


def _messages(problems: list[Any], severity: str = "error") -> list[str]:
    return [p.message for p in problems if p.severity == severity]


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ({"packet_id": 256, "fields": []}, "packet_id must be an integer 0-255"),
        ({"packet_id": True, "fields": []}, "packet_id must be an integer"),
        ({"packet_id": 1, "fields": [{"name": "a", "type": "f16"}]}, "unknown type"),
        (
            {"packet_id": 1, "fields": [{"name": "a", "type": "u8"}, {"name": "a", "type": "u8"}]},
            "duplicate field 'a'",
        ),
        (
            {"packet_id": 1, "fields": [{"name": "a", "type": "u8", "value": 1, "param": "p"}]},
            "both a value and a param",
        ),
        ({"packet_id": 1, "fields": [{"name": "a", "type": "u8", "value": "x"}]}, "number"),
        (
            {"packet_id": 1, "fields": [{"name": f"f{i}", "type": "f64"} for i in range(32)]},
            "at most 255 B",
        ),
        ({"packet_id": 1, "endianness": "middle", "fields": []}, "endianness"),
    ],
)
def test_command_errors_leave_that_command_out(spec: dict[str, Any], expected: str) -> None:
    commands, problems = parse_commands({"bad": spec, **COMMANDS})
    assert list(commands) == ["cmd"]
    assert any(expected in m for m in _messages(problems)), problems
    assert all(p.item == "command 'bad'" and not p.fatal for p in problems)


def test_valid_panel_is_parsed_with_its_defaults() -> None:
    commands, _ = parse_commands(COMMANDS)
    panels, problems = parse_panels({"p": _panel()}, commands, set(COMMANDS))
    assert problems == []
    panel = panels["p"]
    assert panel.column_keys == ("A", "B") and panel.defaults() == {
        "A": {"gain": 1.0},
        "B": {"gain": 1.0},
    }
    assert panel.parameters[0].decimals == 4 and panel.button_column(panel.buttons[0]) == "A"


def test_a_panel_without_columns_has_one_implicit_column() -> None:
    commands, _ = parse_commands(COMMANDS)
    spec = _panel(columns=[], buttons=[{"label": "Go", "command": "cmd", "values": {"id": 1}}])
    panels, problems = parse_panels({"p": spec}, commands, set(COMMANDS))
    assert problems == []
    panel = panels["p"]
    assert panel.column_keys == ("",)
    params = panel.defaults()
    values = resolve_values(
        commands["cmd"], params, panel.button_column(panel.buttons[0]), panel.buttons[0].values
    )
    assert values == {"id": 1.0, "gain": 1.0}


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"buttons": [{"label": "X", "command": "nope"}]}, "command 'nope' is not defined"),
        ({"buttons": [{"label": "X", "command": "broken"}]}, "command 'broken' has errors"),
        ({"buttons": [{"label": "X", "command": "cmd", "values": {"id": 1}}]}, "needs a column"),
        ({"buttons": [{"label": "X", "command": "cmd", "column": "A"}]}, "'id' of 'cmd' gets no"),
        (
            {"buttons": [{"label": "X", "command": "cmd", "column": "C", "values": {"id": 1}}]},
            "column 'C' is not one of",
        ),
        (
            {"buttons": [{"label": "X", "command": "cmd", "column": "A", "values": {"zz": 1}}]},
            "'zz' is not a field",
        ),
        ({"parameters": {}}, "unknown parameter 'gain'"),
        ({"buttons": []}, "non-empty list"),
        ({"columns": ["A", "A"]}, "unique"),
        ({"parameters": {"gain": {"kind": "complex"}}}, "kind must be one of"),
        ({"parameters": {"gain": {"min": 5, "max": 1}}}, "min < max"),
        ({"parameters": {"gain": {"kind": "int", "default": 1.5}}}, "whole number"),
    ],
)
def test_panel_errors_leave_that_panel_out(overrides: dict[str, Any], expected: str) -> None:
    raw = {**COMMANDS, "broken": {"packet_id": 999}}
    commands, _ = parse_commands(raw)
    panels, problems = parse_panels({"p": _panel(**overrides)}, commands, set(raw))
    assert panels == {}
    assert any(expected in m for m in _messages(problems)), problems


def test_panel_warnings_keep_the_panel() -> None:
    raw = {"cmd": {"packet_id": 1, "fields": [{"name": "n", "type": "u8", "param": "gain"}]}}
    commands, _ = parse_commands(raw)
    spec = _panel(
        parameters={"gain": {"default": 5000, "colour": "red"}},
        buttons=[{"label": "Go", "command": "cmd", "column": "A"}],
    )
    panels, problems = parse_panels({"p": spec}, commands, set(raw))
    warnings = _messages(problems, "warning")
    assert "p" in panels and panels["p"].parameters[0].default == 1000.0  # clamped
    assert any("outside" in w for w in warnings) and any("colour" in w for w in warnings)
    assert any("feeds integer field 'n'" in w for w in warnings)


def test_bad_commands_or_panels_never_stop_the_file_loading(tmp_path: Path) -> None:
    doc = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    doc["commands"]["pid_single"]["packet_id"] = 300
    path = tmp_path / "streams.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    loader = StreamConfigLoader(path)

    assert set(loader.list_streams()) == {"pid", "pid_ff", "imu_6axis"}
    assert "pid_single" not in loader.commands and loader.panels == {}
    assert loader.panel_for("pid") is None
    items = {p.item or p.stream for p in loader.problems}
    assert items == {"command 'pid_single'", "panel 'diffbot_pid'", "pid", "pid_ff"}


# --- saving ---


def test_save_document_refuses_errors_and_keeps_a_backup(tmp_path: Path) -> None:
    path = tmp_path / "streams.json"
    original = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    path.write_text(original, encoding="utf-8")
    doc = json.loads(original)

    broken = json.loads(original)
    del broken["streams"]["pid"]["frame"]
    with pytest.raises(InvalidConfigError, match=r"\[pid\]"):
        save_document(path, broken)
    assert path.read_text(encoding="utf-8") == original  # untouched

    doc["streams"]["pid"]["name"] = "Renamed"
    assert save_document(path, doc) == []
    assert json.loads(path.read_text(encoding="utf-8"))["streams"]["pid"]["name"] == "Renamed"
    assert (tmp_path / "streams.json.bak").read_text(encoding="utf-8") == original
    assert not (tmp_path / "streams.json.tmp").exists()


def test_saving_an_unchanged_document_is_byte_identical(tmp_path: Path) -> None:
    path = tmp_path / "streams.json"
    original = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    path.write_text(original, encoding="utf-8")
    save_document(path, StreamConfigLoader(path).data)
    assert path.read_text(encoding="utf-8") == original
