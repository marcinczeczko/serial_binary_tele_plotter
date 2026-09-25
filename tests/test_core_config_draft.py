"""The editor's model (R7.1): edits change only what they're about."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from core.config.draft import PALETTE, StreamDraft

REPO_ROOT = Path(__file__).resolve().parent.parent


def _stream(key: str) -> dict[str, Any]:
    doc = json.loads((REPO_ROOT / "streams.json").read_text(encoding="utf-8"))
    stream: dict[str, Any] = doc["streams"][key]
    return stream


def test_an_untouched_draft_is_the_stream_it_was_made_from() -> None:
    for key in ("pid", "pid_ff", "imu_6axis"):
        original = _stream(key)
        assert json.dumps(StreamDraft(original).to_stream()) == json.dumps(original)


def test_the_draft_is_a_copy() -> None:
    original = _stream("imu_6axis")
    draft = StreamDraft(original)
    draft.name = "Changed"
    assert original["name"] == "IMU 6-Axis Raw"


def test_layout_gives_offsets_and_the_payload_size() -> None:
    draft = StreamDraft(_stream("imu_6axis"))
    slots = draft.layout()
    assert [(s.name, s.offset, s.size) for s in slots[:3]] == [
        ("loop_cntr", 0, 4),
        ("motor", 4, 1),
        ("acc_x", 5, 4),
    ]
    assert draft.payload_size() == 29


def test_renaming_a_field_follows_its_signals_time_base_and_simulator() -> None:
    draft = StreamDraft(_stream("imu_6axis"))
    index = draft.field_names().index("acc_x")

    assert draft.rename_field(index, "accel_x") is None

    data = draft.to_stream()
    assert data["signals"]["acc_x"]["field"] == "accel_x"  # the signal key stays
    assert list(data["sim"]["fields"])[1] == "accel_x"  # same place in the simulator
    assert draft.rename_field(index, "acc_y") == "there is already a field 'acc_y'"
    assert draft.rename_field(index, "9lives") is not None
    draft.rename_field(0, "tick")
    assert draft.time_value("field") == "tick"


def test_removing_a_field_removes_its_signals() -> None:
    draft = StreamDraft(_stream("imu_6axis"))
    draft.remove_field(draft.field_names().index("acc_x"))
    data = draft.to_stream()
    assert "acc_x" not in data["signals"]
    assert "acc_x" not in data["sim"]["fields"]


def test_adding_and_moving_fields() -> None:
    draft = StreamDraft(_stream("imu_6axis"))
    index = draft.add_field(2, "acc_x", "i16")  # the name is taken: made free
    assert draft.field_names()[index] == "acc_x_2"
    draft.move_field(index, 0)
    assert draft.field_names()[:2] == ["acc_x_2", "loop_cntr"]
    assert draft.payload_size() == 31


def test_plotting_a_field_adds_a_signal_named_after_it_in_a_free_color() -> None:
    stream = _stream("imu_6axis")
    draft = StreamDraft(stream)

    key = draft.add_signal("gyro_y", lane="gyro")

    sig = draft.signal(key)
    assert key == "gyro_y" and sig["label"] == "gyro_y" and sig["group"] == "gyro"
    used = {s["color"].lower() for s in stream["signals"].values()}
    assert sig["color"] in PALETTE and sig["color"].lower() not in used
    assert draft.add_signal("gyro_y") == "gyro_y_2"  # a second signal on the same field


def test_signal_edits_touch_only_their_keys() -> None:
    original = _stream("imu_6axis")
    draft = StreamDraft(original)

    draft.set_signal("acc_x", "label", "Accel X")
    draft.set_signal("acc_x", "width", 2)
    draft.set_signal("acc_x", "group", "")

    sig = draft.to_stream()["signals"]["acc_x"]
    expected = copy.deepcopy(original["signals"]["acc_x"])
    expected["label"], expected["line"]["width"] = "Accel X", 2
    del expected["group"]
    assert sig == expected


def test_time_keys_are_written_only_when_present_or_changed() -> None:
    bare = _stream("imu_6axis")
    del bare["time"]
    draft = StreamDraft(bare)
    draft.set_time("field", "loop_cntr")
    draft.set_time("step", 1)
    assert "time" not in draft.to_stream()
    draft.set_time("scale_s", 1e-6)
    assert draft.to_stream()["time"] == {"scale_s": 1e-6}

    kept = StreamDraft({**bare, "time": {"step": 1, "note": "kept", "scale_s": 0.005}})
    kept.set_time("scale_s", 0.01)
    assert kept.to_stream()["time"] == {"step": 1, "note": "kept", "scale_s": 0.01}


def test_naming_a_new_lane_adds_it_last() -> None:
    draft = StreamDraft(_stream("imu_6axis"))
    draft.set_lane_label("mag", "Magnetometer [uT]")
    draft.set_lane_label("accel", "Accel [g]")
    groups = draft.to_stream()["groups"]
    assert groups["mag"] == {"label": "Magnetometer [uT]", "order": 3}
    assert groups["accel"] == {"label": "Accel [g]", "order": 1}
