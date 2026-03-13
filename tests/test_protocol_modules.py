import struct

import pytest

import core.protocol
from core.protocol.constants import MAGIC_0, MAGIC_1, STRUCT_TYPE_MAP
from core.protocol.crc import calculate_crc8
from core.protocol.decoder import FrameDecoder
from core.protocol.handler import ProtocolHandler


def test_constants_have_expected_types():
    assert MAGIC_0 == 0xAA
    assert MAGIC_1 == 0x55
    assert "u8" in STRUCT_TYPE_MAP
    assert STRUCT_TYPE_MAP["u8"][0] == "B"


def test_crc8_known_vector():
    data = b"123456789"
    assert calculate_crc8(data) == 0xF4


def test_decoder_decode_payload():
    fields = [
        {"name": "a", "type": "u8"},
        {"name": "b", "type": "i16"},
    ]
    decoder = FrameDecoder("little", fields)
    payload = struct.pack("<Bh", 7, -2)
    decoded = decoder.decode(payload)
    assert decoded["a"] == 7
    assert decoded["b"] == -2
    assert decoder.size == struct.calcsize("<Bh")


def test_decoder_unknown_type_raises():
    with pytest.raises(ValueError):
        FrameDecoder("little", [{"name": "x", "type": "bad"}])


def _build_frame(p_type: int, payload: bytes) -> bytes:
    header = bytes([MAGIC_0, MAGIC_1, p_type, len(payload)])
    h_crc = calculate_crc8(header)
    p_crc = calculate_crc8(payload)
    return header + bytes([h_crc]) + payload + bytes([p_crc])


def test_process_valid_frame():
    handler = ProtocolHandler()
    cfg = {
        "name": "Test",
        "frame": {
            "stream_id": 0x20,
            "fields": [{"name": "x", "type": "u8"}],
        },
    }
    handler.configure(cfg)
    payload = struct.pack("<B", 42)
    frame = _build_frame(0x20, payload)
    handler.add_data(frame)
    frames = list(handler.process_available_frames())
    assert len(frames) == 1
    assert frames[0]["x"] == 42


def test_type_mismatch_ignored():
    handler = ProtocolHandler()
    cfg = {
        "name": "Test",
        "frame": {
            "stream_id": 0x20,
            "fields": [{"name": "x", "type": "u8"}],
        },
    }
    handler.configure(cfg)
    payload = struct.pack("<B", 1)
    frame = _build_frame(0x21, payload)
    handler.add_data(frame)
    frames = list(handler.process_available_frames())
    assert frames == []


def test_empty_payload_frame():
    handler = ProtocolHandler()
    cfg = {
        "name": "Empty",
        "frame": {
            "stream_id": 0x30,
            "fields": [],
        },
    }
    handler.configure(cfg)
    frame = _build_frame(0x30, b"")
    handler.add_data(frame)
    frames = list(handler.process_available_frames())
    assert len(frames) == 1
    assert frames[0] == {}


def test_bad_payload_crc_dropped():
    handler = ProtocolHandler()
    cfg = {
        "name": "Test",
        "frame": {
            "stream_id": 0x20,
            "fields": [{"name": "x", "type": "u8"}],
        },
    }
    handler.configure(cfg)
    payload = struct.pack("<B", 99)
    frame = _build_frame(0x20, payload)
    # Corrupt last byte (payload CRC)
    frame = frame[:-1] + bytes([(frame[-1] + 1) % 256])
    handler.add_data(frame)
    frames = list(handler.process_available_frames())
    assert frames == []
