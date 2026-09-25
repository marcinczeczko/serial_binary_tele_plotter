"""C structs in and out (R7.2, R7.3): what's read, what's refused, and the round trip."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.config import validate_stream
from core.config.cstruct import (
    parse_c_struct,
    replace_fields,
    stream_from_struct,
    to_c_struct,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

ODOM = """
// telemetry.h
#define TELEM_ID_ODOM 0x04
#define N_WHEELS 2

typedef struct __attribute__((packed)) {
    uint32_t loop_cntr;
    float    x_m, y_m;          // pose
    float    heading_rad;
    int16_t  wheel_ticks[N_WHEELS];  /* left, right */
    uint16_t batt_mv;
    uint8_t  flags;
} odom_frame_t;
"""


def test_reads_a_packed_struct_with_its_id_arrays_and_several_names_per_line() -> None:
    parsed = parse_c_struct(ODOM)

    assert parsed.name == "odom_frame_t"
    assert parsed.packed
    assert (parsed.stream_id, parsed.id_source) == (4, "TELEM_ID_ODOM")
    assert parsed.fields == [
        ("loop_cntr", "u32"),
        ("x_m", "f32"),
        ("y_m", "f32"),
        ("heading_rad", "f32"),
        ("wheel_ticks_0", "i16"),
        ("wheel_ticks_1", "i16"),
        ("batt_mv", "u16"),
        ("flags", "u8"),
    ]
    assert parsed.size == 23
    assert parsed.problems == [] and parsed.notes == []


def test_bare_member_lines_are_read_as_packed() -> None:
    parsed = parse_c_struct("uint32_t loop_cntr;\nuint8_t motor;\nfloat a, b;")

    assert parsed.name is None and parsed.packed
    assert parsed.fields == [("loop_cntr", "u32"), ("motor", "u8"), ("a", "f32"), ("b", "f32")]


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [
        ("unsigned char", "u8"),
        ("signed char", "i8"),
        ("unsigned short int", "u16"),
        ("short", "i16"),
        ("unsigned", "u32"),
        ("int", "i32"),
        ("bool", "u8"),
        ("double", "f64"),
        ("volatile const uint64_t", "u64"),
    ],
)
def test_reads_the_usual_c_spellings(spelling: str, expected: str) -> None:
    parsed = parse_c_struct(f"uint32_t loop_cntr; {spelling} value;")
    assert parsed.fields[1] == ("value", expected)


def test_an_unpacked_struct_keeps_the_compilers_padding_as_pad_fields() -> None:
    parsed = parse_c_struct(
        "struct s { uint32_t loop_cntr; uint8_t a; uint16_t b; double c; uint8_t d; };"
    )

    assert not parsed.packed
    names = [n for n, _ in parsed.fields]
    assert names == [
        "loop_cntr", "a", "_pad5", "b", "c", "d",
        "_pad17", "_pad18", "_pad19", "_pad20", "_pad21", "_pad22", "_pad23",
    ]  # fmt: skip
    assert parsed.size == 24  # what sizeof() gives on a 32-bit MCU
    assert "padding" in parsed.notes[0]


def test_what_cant_be_read_is_reported_and_left_out() -> None:
    parsed = parse_c_struct(
        """struct s {
            uint32_t loop_cntr;
            long big;
            float *ptr;
            uint8_t mode : 3;
            struct { int a; } inner;
            my_enum_t state;
            float arr[SIZE];
            float ok;
        };"""
    )

    assert [n for n, _ in parsed.fields] == ["loop_cntr", "ok"]
    text = "\n".join(parsed.problems)
    for expected in ("long big", "pointers", "bit-fields", "nested", "my_enum_t", "SIZE"):
        assert expected in text


def test_a_missing_counter_and_an_oversized_frame_are_problems() -> None:
    assert "loop_cntr" in parse_c_struct("float a;").problems[0]
    too_big = parse_c_struct("uint32_t loop_cntr; double d[40];")
    assert any("255" in p for p in too_big.problems)


def test_several_id_defines_are_not_guessed() -> None:
    parsed = parse_c_struct("#define A_ID 1\n#define B_ID 2\nuint32_t loop_cntr;")
    assert parsed.stream_id is None


def test_a_new_stream_plots_every_field_but_the_counter_and_padding() -> None:
    parsed = parse_c_struct("struct s { uint32_t loop_cntr; uint8_t a; float b; };")

    stream = stream_from_struct(parsed, "Odometry", 4, scale_s=0.005)

    assert stream["frame"]["stream_id"] == 4
    assert [f["name"] for f in stream["frame"]["fields"]] == [
        "loop_cntr", "a", "_pad5", "_pad6", "_pad7", "b",
    ]  # fmt: skip
    assert list(stream["signals"]) == ["a", "b"]
    assert stream["signals"]["b"]["label"] == "b"
    assert stream["signals"]["a"]["color"] != stream["signals"]["b"]["color"]
    assert stream["time"] == {"field": "loop_cntr", "scale_s": 0.005}
    assert [p for p in validate_stream("odom", stream) if p.severity == "error"] == []


def test_replacing_fields_keeps_the_settings_of_fields_that_stay() -> None:
    doc = json.loads((REPO_ROOT / "streams.json").read_text(encoding="utf-8"))
    imu = doc["streams"]["imu_6axis"]
    parsed = parse_c_struct(
        "uint32_t loop_cntr; uint8_t motor; float acc_x, acc_y, acc_z, gyro_x, gyro_y, temp;"
    )

    stream, summary = replace_fields(imu, parsed)

    assert summary == "7 field(s) kept, 1 added, 1 removed"
    assert stream["signals"]["acc_x"] == imu["signals"]["acc_x"]  # label, color, lane kept
    assert "gyro_z" not in stream["signals"]  # its field is gone
    assert "gyro_y" not in stream["signals"]  # wasn't plotted before, still isn't
    assert stream["signals"]["temp"]["field"] == "temp"
    assert "gyro_z" not in stream["sim"]["fields"]
    assert [p for p in validate_stream("imu", stream) if p.severity == "error"] == []


def test_every_bundled_stream_round_trips_through_c() -> None:
    doc = json.loads((REPO_ROOT / "streams.json").read_text(encoding="utf-8"))
    for key, stream in doc["streams"].items():
        header = to_c_struct(key, stream)
        parsed = parse_c_struct(header)
        assert parsed.fields == [(f["name"], f["type"]) for f in stream["frame"]["fields"]]
        assert parsed.stream_id == stream["frame"]["stream_id"]
        assert parsed.packed and parsed.problems == []
        assert f"sizeof({key}_t) == {parsed.size}" in header


def test_a_big_endian_stream_says_so_in_the_header() -> None:
    stream = {"name": "S", "frame": {"stream_id": 9, "endianness": "big", "fields": []}}
    assert "Big-endian" in to_c_struct("s", stream)
