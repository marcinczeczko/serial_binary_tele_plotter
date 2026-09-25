"""Line patterns from console output (R8.4): the inference behind "From console output…"."""

from __future__ import annotations

from typing import Any

from core.config import validate_stream
from core.config.infer_lines import infer_patterns, split_line
from core.protocol.text_line import TextLineDecoder

# The canvas example (`PasteLine`).
CONSOLE = """\
boot ok, fw 1.4.2
IMU,1204,0.12,-0.03,0.98
IMU,1214,0.13,-0.02,0.97
ENV t=24.5C h=41%
ENC l=-12 r=15
IMU,1224,0.11,-0.04,0.99
IMU,1234,0.12,-0.03,0.98
ENV t=24.6C h=41%
ENC l=-14 r=17
IMU,1244,0.12,-0.02,0.98
IMU,1254,0.13,-0.03,0.97
ENC l=-13 r=16
"""


def _types(pattern: Any) -> dict[str, str]:
    return {v.name: v.type for v in pattern.values}


def test_the_canvas_example() -> None:
    result = infer_patterns(CONSOLE.splitlines())
    assert [p.pattern for p in result.patterns] == [
        "IMU,{v1},{v2},{v3},{v4}",
        "ENV t={t}C h={h}%",
        "ENC l={l} r={r}",
    ]
    imu, env, enc = result.patterns
    assert [p.name for p in result.patterns] == ["IMU", "ENV", "ENC"]
    assert _types(imu) == {"v1": "u32", "v2": "f32", "v3": "f32", "v4": "f32"}
    assert _types(env) == {"t": "f32", "h": "u32"}
    assert _types(enc) == {"l": "i32", "r": "u32"}
    assert imu.values[0].note == "X axis, step 10"
    assert imu.values[1].range_text == "0.11 … 0.13" and env.values[1].range_text == "41"
    assert env.values[1].axis_step is None  # constant: not an axis
    assert result.seen_once == ["boot ok, fw 1.4.2"]
    assert result.line_count == 12


def test_each_stream_decodes_the_lines_it_came_from() -> None:
    result = infer_patterns(CONSOLE.splitlines())
    streams = {p.name.lower(): p.stream() for p in result.patterns}
    for key, stream in streams.items():
        assert validate_stream(key, stream, "text") == []
    decoder = TextLineDecoder()
    decoder.configure(streams)  # type: ignore[arg-type]
    records = decoder.feed(CONSOLE.encode())
    assert records["imu"]["v1"].tolist() == [1204, 1214, 1224, 1234, 1244, 1254]
    assert records["env"]["t"].tolist() == [24.5, 24.600000381469727]
    assert records["enc"]["l"].tolist() == [-12, -14, -13]
    assert decoder.stats.lines_unmatched == 1  # the boot line


def test_the_stream_uses_the_axis_and_plots_the_rest() -> None:
    imu = infer_patterns(CONSOLE.splitlines()).patterns[0].stream()
    assert imu["time"] == {"field": "v1", "step": 10}
    assert sorted(imu["signals"]) == ["v2", "v3", "v4"]
    assert imu["name"] == "IMU"


def test_a_millisecond_counter_gets_one_ms_per_tick() -> None:
    lines = [f"ms={t} x={t * 0.5}" for t in range(0, 100, 20)]
    stream = infer_patterns(lines).patterns[0].stream()
    assert stream["time"] == {"field": "ms", "scale_s": 0.001, "step": 20}


def test_numbers_inside_words_are_fixed_text() -> None:
    assert split_line("v1=3 info=nan fw1.2") == (("v1=", " info=", " fw1.2"), ["3", "nan"])
    assert split_line("a -5,+.5e-3") == (("a ", ",", ""), ["-5", "+.5e-3"])
    assert split_line("1-2") == (("", "-", ""), ["1", "2"])


def test_an_irregular_counter_is_not_an_axis() -> None:
    lines = ["S,1,0.5", "S,2,0.5", "S,9,0.5", "S,10,0.5"]
    pattern = infer_patterns(lines).patterns[0]
    assert pattern.values[0].type == "u32" and pattern.values[0].axis_step is None


def test_braces_and_unnamed_streams() -> None:
    lines = ["{1} 2", "{3} 4", "  ", ""]
    result = infer_patterns(lines)
    assert result.line_count == 2
    assert result.patterns[0].pattern == "{{{v1}}} {v2}"
    assert result.patterns[0].name == "stream 1"
