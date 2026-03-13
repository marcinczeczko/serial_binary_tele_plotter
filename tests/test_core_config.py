import json

import pytest

from core.config import StreamConfigLoader


def test_load_valid_config_and_default_panel_type(tmp_path):
    payload = {
        "streams": {
            "s1": {
                "name": "Test Stream",
                "frame": {"stream_id": 1, "fields": []},
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
