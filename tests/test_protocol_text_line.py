"""Text-line streams (R8.3): the pattern grammar and `TextLineDecoder`."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from core.protocol.link import TEXT, make_link_decoder
from core.protocol.text_line import (
    LINE_FIELD,
    MAX_LINE_BYTES,
    PatternError,
    TextLineDecoder,
    format_line,
    parse_pattern,
)


def _stream(pattern: str, *fields: tuple[str, str], **extra: Any) -> dict[str, Any]:
    return {
        "name": pattern.split(",")[0],
        "frame": {"pattern": pattern, "fields": [{"name": n, "type": t} for n, t in fields]},
        **extra,
    }


IMU = _stream("IMU,{ms},{ax},{ay}", ("ms", "u32"), ("ax", "f32"), ("ay", "f32"))
ENV = _stream("ENV t={t}C h={h}%", ("t", "f32"), ("h", "u32"))


def _decoder(**streams: dict[str, Any]) -> TextLineDecoder:
    decoder = TextLineDecoder()
    decoder.configure(streams or {"imu": IMU, "env": ENV})  # type: ignore[arg-type]
    return decoder


# --- pattern grammar ---------------------------------------------------------------------


def test_a_pattern_is_fixed_text_and_slots() -> None:
    pattern = parse_pattern("ENV t={t}C h={h}%")
    assert pattern.slots == ("t", "h")
    assert pattern.tokens[0] == "ENV t="


def test_double_braces_are_literal() -> None:
    pattern = parse_pattern("{{{a}}}")
    assert pattern.slots == ("a",)
    assert pattern.regex.fullmatch("{1.5}")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("{a}{b}", "need fixed text between them"),
        ("{a},{a}", "appears twice"),
        ("no slots", "at least one"),
        ("{1x}", "not a slot"),
        ("{a", "unclosed"),
        ("a}", "unmatched"),
        ("{_line}", "reserved"),
    ],
)
def test_bad_patterns_say_why(text: str, message: str) -> None:
    with pytest.raises(PatternError, match=message):
        parse_pattern(text)


def test_a_whitespace_run_matches_any_padding() -> None:
    pattern = parse_pattern("x = {x}  y = {y}")
    assert pattern.regex.fullmatch("x =   1.00 y=2") is None  # the run needs at least one
    assert pattern.regex.fullmatch("x =   1.00\t y =    2")


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("1", 1.0),
        ("-2.5", -2.5),
        ("+3.", 3.0),
        (".5", 0.5),
        ("-.5e-3", -0.0005),
        ("1E3", 1000.0),
        ("inf", math.inf),
        ("-Infinity", -math.inf),
    ],
)
def test_numbers(text: str, value: float) -> None:
    records = _decoder(s=_stream("v={v}", ("v", "f64"))).feed(f"v={text}\n".encode())
    assert records["s"]["v"].tolist() == [value]


def test_nan_and_an_empty_slot_are_gaps() -> None:
    decoder = _decoder(s=_stream("{a},{b},{c}", ("a", "f32"), ("b", "f32"), ("c", "f32")))
    records = decoder.feed(b"1,,3\nNaN,2,\n")["s"]
    assert records["a"][0] == 1 and math.isnan(records["b"][0]) and records["c"][0] == 3
    assert math.isnan(records["a"][1]) and math.isnan(records["c"][1])


def test_format_line_prints_what_the_pattern_matches() -> None:
    pattern = parse_pattern("IMU,{ms},{ax}")
    line = format_line(pattern, {"ms": np.uint32(120), "ax": 0.1234567891})
    assert line == "IMU,120,0.123457"
    assert pattern.regex.fullmatch(line)


# --- decoding ----------------------------------------------------------------------------


def test_text_is_a_link_format() -> None:
    assert isinstance(make_link_decoder(TEXT), TextLineDecoder)


def test_lines_decode_into_records_per_stream() -> None:
    decoder = _decoder()
    out = decoder.feed(b"IMU,10,0.5,-1\r\nENV t=24.5C h=41%\r\nIMU,20,0.25,-2\r\n")
    assert out["imu"]["ms"].tolist() == [10, 20]
    assert out["imu"]["ax"].tolist() == [0.5, 0.25]
    assert out["imu"].dtype["ms"] == np.uint32
    assert out["env"]["t"].tolist() == [24.5] and out["env"]["h"].tolist() == [41]
    assert decoder.stats.lines_rx == 3 and decoder.stats.frames_decoded == 3
    assert decoder.stats.frames_by_id == {}


def test_the_first_matching_pattern_wins() -> None:
    wide = _stream("{a},{b}", ("a", "f32"), ("b", "f32"))
    narrow = _stream("1,{b}", ("b", "f32"))
    out = _decoder(wide=wide, narrow=narrow).feed(b"1,2\n")
    assert list(out) == ["wide"]


def test_an_integer_slot_needs_an_integral_value() -> None:
    decoder = _decoder()
    out = decoder.feed(b"IMU,1.5,0,0\nIMU,,0,0\nIMU,nan,0,0\nIMU,1e3,0,0\nENV t=1C h=-1%\n")
    assert out["imu"]["ms"].tolist() == [1000]  # 1e3 is integral
    assert "env" not in out  # -1 doesn't fit a u32
    assert decoder.stats.value_errors == 4
    assert decoder.stats.frames_decoded == 1


def test_lines_split_across_chunks_are_joined() -> None:
    decoder = _decoder()
    assert decoder.feed(b"IMU,1,0.") == {}
    out = decoder.feed(b"5,1\r")
    assert out == {}
    out = decoder.feed(b"\nIMU,2,0,0\n")
    assert out["imu"]["ax"].tolist() == [0.5, 0.0]


def test_blank_lines_are_skipped_silently() -> None:
    decoder = _decoder()
    decoder.feed(b"\r\n\n   \nIMU,1,0,0\n")
    assert decoder.stats.lines_rx == 1 and decoder.stats.lines_unmatched == 0


def test_unmatched_and_non_ascii_lines_are_counted() -> None:
    decoder = _decoder()
    out = decoder.feed(b"Booting v1.2...\nIMU,1,0\xff5,0\nIMU,2,0,0\n")
    assert out["imu"]["ms"].tolist() == [2]
    assert decoder.stats.lines_unmatched == 2
    assert decoder.stats.lines_rx == 3


def test_an_overlong_line_is_counted_once_and_the_buffer_stays_bounded() -> None:
    decoder = _decoder()
    for _ in range(10):
        decoder.feed(b"x" * 500)
        assert len(decoder._buf) <= MAX_LINE_BYTES
    out = decoder.feed(b"yyy\nIMU,5,0,0\n")
    assert decoder.stats.lines_overlong == 1
    assert out["imu"]["ms"].tolist() == [5]  # the next line decodes
    decoder.feed(b"z" * (MAX_LINE_BYTES + 1) + b"\n")  # a whole overlong line in one chunk
    assert decoder.stats.lines_overlong == 2
    assert decoder.stats.lines_unmatched == 0


def test_line_counts_per_stream() -> None:
    decoder = _decoder()
    out = decoder.feed(b"IMU,1,0,0\nENV t=1C h=1%\nIMU,2,0,0\nIMU,3,0,0\n")
    assert out["imu"][LINE_FIELD].tolist() == [0, 1, 2]
    assert out["env"][LINE_FIELD].tolist() == [0]
    assert decoder.feed(b"ENV t=1C h=1%\n")["env"][LINE_FIELD].tolist() == [1]


def test_reset_clears_the_buffer_stats_and_line_counts() -> None:
    decoder = _decoder()
    decoder.feed(b"IMU,1,0,0\nbanner\nIMU,2,0")
    decoder.reset()
    assert decoder.stats.lines_rx == 0 and decoder.stats.bytes_rx == 0
    out = decoder.feed(b",0\nIMU,3,0,0\n")  # the half line from before the reset is gone
    assert out["imu"]["ms"].tolist() == [3]
    assert out["imu"][LINE_FIELD].tolist() == [0]
    assert decoder.stats.lines_unmatched == 1  # ",0"


def test_an_integer_time_slot_tracks_gaps_and_resets() -> None:
    stream = _stream(
        "IMU,{ms},{ax}", ("ms", "u32"), ("ax", "f32"), time={"field": "ms", "step": 10}
    )
    decoder = _decoder(imu=stream)
    decoder.feed(b"IMU,10,0\nIMU,20,0\nIMU,50,0\nIMU,0,0\n")
    stats = decoder.stats
    assert (stats.counter_gaps, stats.counter_missing, stats.counter_resets) == (1, 2, 1)


def test_a_stream_without_a_pattern_is_not_decoded() -> None:
    binary = {"name": "bin", "frame": {"stream_id": 1, "fields": [{"name": "v", "type": "u8"}]}}
    decoder = _decoder(imu=IMU, bin=binary)
    assert list(decoder.feed(b"IMU,1,0,0\n")) == ["imu"]


def test_pattern_and_fields_must_agree() -> None:
    with pytest.raises(ValueError, match="don't match"):
        _decoder(s=_stream("{a},{b}", ("b", "f32"), ("a", "f32")))


def test_the_newest_lines_are_kept_for_the_editor() -> None:
    decoder = _decoder()
    decoder.feed(b"IMU,1,0,0\nIMU,2,0,0\r\nbanner\nENV t=1C h=1%\n")
    assert decoder.stats.last_lines == {"imu": "IMU,2,0,0", "env": "ENV t=1C h=1%"}
    assert decoder.stats.last_unmatched == "banner"
    snap = decoder.stats.snapshot()
    decoder.feed(b"IMU,3,0,0\n")
    assert snap.last_lines["imu"] == "IMU,2,0,0"  # a snapshot doesn't follow


def test_pattern_text_writes_tokens_back() -> None:
    from core.protocol.text_line import pattern_text

    for text in ("ENV t={t}C h={h}%", "{{{a}}} x", "{a} , {b}"):
        assert pattern_text(parse_pattern(text).tokens) == text
