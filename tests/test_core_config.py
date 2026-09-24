import json

import pytest

from core.config import StreamConfigLoader, validate_config, validate_stream


def test_load_valid_config_and_default_panel_type(tmp_path):
    payload = {
        "streams": {
            "s1": {
                "name": "Test Stream",
                "frame": {"stream_id": 1, "fields": [{"name": "loop_cntr", "type": "u32"}]},
                "signals": {},
            }
        }
    }
    path = tmp_path / "streams.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loader = StreamConfigLoader(str(path))
    streams = loader.list_streams()
    assert "s1" in streams
    assert streams["s1"]["panel_type"] == "none"


def test_invalid_json_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not-valid-json", encoding="utf-8")

    with pytest.raises(ValueError):
        StreamConfigLoader(str(path))


def test_missing_streams_raises(tmp_path):
    path = tmp_path / "missing.json"
    path.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")

    with pytest.raises(ValueError):
        StreamConfigLoader(str(path))


def _stream(**overrides):
    stream = {
        "name": "S",
        "panel_type": "none",
        "frame": {
            "stream_id": 3,
            "endianness": "little",
            "fields": [{"name": "loop_cntr", "type": "u32"}, {"name": "x", "type": "f32"}],
        },
        "signals": {"x": {"label": "X", "field": "x"}},
    }
    stream.update(overrides)
    return stream


def _messages(problems, severity="error"):
    return [p.message for p in problems if p.severity == severity]


def test_valid_stream_has_no_problems():
    assert validate_stream("s", _stream()) == []


def test_repo_streams_json_is_valid():
    with open("streams.json", encoding="utf-8") as f:
        problems = validate_config(json.load(f))
    assert [str(p) for p in problems] == []


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        ({"stream_id": 300, "fields": [{"name": "loop_cntr", "type": "u32"}]}, "0-255"),
        (
            {
                "stream_id": 1,
                "endianness": "middle",
                "fields": [{"name": "loop_cntr", "type": "u32"}],
            },
            "endianness",
        ),
        ({"stream_id": 1, "fields": []}, "non-empty list"),
        ({"stream_id": 1, "fields": [{"name": "loop_cntr", "type": "u128"}]}, "unknown type"),
        ({"stream_id": 1, "fields": [{"name": "x", "type": "f32"}]}, "must contain 'loop_cntr'"),
        (
            {
                "stream_id": 1,
                "fields": [
                    {"name": "loop_cntr", "type": "u32"},
                    {"name": "loop_cntr", "type": "u8"},
                ],
            },
            "duplicate field",
        ),
        (
            {
                "stream_id": 1,
                "fields": [{"name": "loop_cntr", "type": "u32"}]
                + [{"name": f"v{i}", "type": "f64"} for i in range(32)],
            },
            "at most 255 B",
        ),
    ],
)
def test_frame_errors(frame, expected):
    errors = _messages(validate_stream("s", _stream(frame=frame, signals={})))
    assert any(expected in e for e in errors), errors


def test_signal_mapped_to_missing_field_is_an_error():
    stream = _stream(signals={"ghost": {"label": "G", "field": "nope"}})
    assert any("'nope'" in e for e in _messages(validate_stream("s", stream)))


def test_warnings_do_not_block_loading(tmp_path):
    stream = _stream(panel_type="fancy")
    stream["frame"]["fields"] = [{"name": "x", "type": "f32"}, {"name": "loop_cntr", "type": "u16"}]
    path = tmp_path / "streams.json"
    path.write_text(json.dumps({"streams": {"ok": stream}}), encoding="utf-8")

    loader = StreamConfigLoader(path)

    assert "ok" in loader.list_streams()
    warnings = _messages(loader.problems, "warning")
    assert len(warnings) == 3  # unknown panel_type, loop_cntr not first, loop_cntr not u32


def test_loader_excludes_only_broken_streams(tmp_path):
    broken = _stream(signals={"ghost": {"field": "nope"}})
    path = tmp_path / "streams.json"
    path.write_text(json.dumps({"streams": {"good": _stream(), "bad": broken}}), encoding="utf-8")

    loader = StreamConfigLoader(path)

    assert list(loader.list_streams()) == ["good"]
    assert [p.stream for p in loader.problems] == ["bad"]
    assert "ERROR: [bad]" in str(loader.problems[0])


def test_resolve_config_path_prefers_cli_then_remembered_then_default(tmp_path):
    from core.config import DEFAULT_CONFIG_PATH, resolve_config_path

    remembered = tmp_path / "last.json"
    remembered.write_text("{}", encoding="utf-8")

    assert resolve_config_path("x/../mine.json", remembered) == (tmp_path.cwd() / "mine.json")
    assert resolve_config_path(None, remembered) == remembered.resolve()
    assert resolve_config_path(None, tmp_path / "gone.json") == DEFAULT_CONFIG_PATH
    assert resolve_config_path(None, None) == DEFAULT_CONFIG_PATH
    assert DEFAULT_CONFIG_PATH.is_file()  # bundled next to the code, not the CWD


def test_loader_does_not_mutate_the_raw_document(tmp_path):
    stream = _stream()
    del stream["panel_type"]
    path = tmp_path / "streams.json"
    path.write_text(json.dumps({"streams": {"s": stream}}), encoding="utf-8")

    loader = StreamConfigLoader(path)

    assert loader.list_streams()["s"]["panel_type"] == "none"
    assert "panel_type" not in loader.data["streams"]["s"]


def test_shared_stream_id_with_ambiguous_layout_is_a_warning():
    same = _stream()
    other_size = _stream()
    other_size["frame"]["fields"] = [
        {"name": "loop_cntr", "type": "u32"},
        {"name": "y", "type": "f64"},
    ]
    other_size["signals"] = {}
    ambiguous = _stream()
    ambiguous["frame"]["fields"] = [
        {"name": "loop_cntr", "type": "u32"},
        {"name": "x", "type": "i32"},
    ]

    problems = validate_config(
        {"streams": {"a": _stream(), "b": same, "c": other_size, "d": ambiguous}}
    )

    assert [(p.severity, p.stream) for p in problems] == [("warning", "d")]
    assert "only 'a' is decoded" in problems[0].message
