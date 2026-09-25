"""Byte-level simulator (R2.7, A6): frames from stream definitions, through the real parser."""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from core.config import StreamConfigLoader, validate_stream
from core.protocol.commands import CommandDef, encode_command
from core.protocol.frame_parser import FrameParser
from core.protocol.router import StreamRouter
from core.simulation.synth import FrameSynth, Wave
from core.transport import SimTransport, TransportError
from core.types import StreamConfig

REPO_STREAMS = Path(__file__).resolve().parent.parent / "streams.json"


def _decode(streams: dict[str, StreamConfig], key: str, data: bytes) -> tuple[np.ndarray, Any]:
    parser = FrameParser()
    router = StreamRouter(parser.stats)
    router.configure(streams)
    return router.route(parser.feed(data))[key], parser.stats


def _stream(fields: list[tuple[str, str]], **extra: Any) -> StreamConfig:
    stream: dict[str, Any] = {
        "name": "S",
        "frame": {"stream_id": 7, "fields": [{"name": n, "type": t} for n, t in fields]},
        "signals": {n: {"field": n} for n, _ in fields},
        **extra,
    }
    return cast(StreamConfig, stream)


@pytest.mark.parametrize("key", list(StreamConfigLoader(REPO_STREAMS).list_streams()))
def test_every_repo_stream_simulates_plausible_data_through_the_parser(key: str) -> None:
    streams = StreamConfigLoader(REPO_STREAMS).list_streams()
    data = FrameSynth(streams[key], seed=3).frames(0, 2000)  # 10 s at 5 ms

    records, stats = _decode(streams, key, data)

    assert len(records) == 2000
    assert stats.errors == 0 and stats.discarded_bytes == 0 and stats.counter_gaps == 0
    for sig_id, sig in streams[key]["signals"].items():
        values = records[sig["field"]].astype(np.float64)
        assert np.isfinite(values).all(), sig_id
        if sig["field"].endswith("_aw_term"):
            continue  # anti-windup is zero until the output saturates (tested below)
        assert np.abs(values).max() > 0, f"{sig_id} is all zero"
        assert values.std() > 0, f"{sig_id} is flat"


def test_counters_count_frames_and_wrap_like_the_mcu() -> None:
    stream = _stream(
        [("loop_cntr", "u8"), ("t_us", "i16"), ("x", "f32")],
        time={"field": "t_us", "scale_s": 1e-6, "step": 1000},
    )
    synth = FrameSynth(stream, seed=0)
    records, _ = _decode({"s": stream}, "s", synth.frames(0, 150) + synth.frames(150, 150))

    k = np.arange(300)
    assert records["loop_cntr"].tolist() == (k % 256).tolist()
    expected_t = ((k * 1000 + 32768) % 65536 - 32768).tolist()  # i16 wraps into negatives
    assert records["t_us"].tolist() == expected_t
    assert synth.period_s == pytest.approx(0.001)


def test_field_waves_follow_their_spec() -> None:
    stream = _stream(
        [("loop_cntr", "u32"), ("c", "f32"), ("s", "f64"), ("q", "f32"), ("n", "u8"), ("u", "u8")],
        time={"scale_s": 0.01},
        sim={
            "fields": {
                "c": {"wave": "const", "offset": 2.5},
                "s": {"wave": "sine", "amp": 3, "freq_hz": 1, "offset": 1, "phase_deg": 90},
                "q": {"wave": "step", "amp": 2, "freq_hz": 1},
                "n": {"wave": "counter", "amp": 3},
                "u": {"wave": "const", "offset": 1000},  # clipped to the field's range
            }
        },
    )
    records, _ = _decode({"s": stream}, "s", FrameSynth(stream, seed=0).frames(0, 100))

    t = np.arange(100) * 0.01
    assert (records["c"] == np.float32(2.5)).all()
    assert records["s"] == pytest.approx(1 + 3 * np.cos(2 * math.pi * t))
    assert set(records["q"].tolist()) == {2.0, -2.0}
    assert records["n"].tolist() == [min(3 * i, 255) for i in range(100)]
    assert (records["u"] == 255).all()


def test_noise_and_default_waves() -> None:
    wave = Wave("noise", noise=2.0)
    values = wave.evaluate(np.arange(20000), np.zeros(20000), np.random.default_rng(0))
    assert values.std() == pytest.approx(2.0, rel=0.05)
    assert abs(values.mean()) < 0.1
    # Fields without a spec differ from each other (no two traces on top of each other).
    stream = _stream([("loop_cntr", "u32"), ("a", "f32"), ("b", "f32"), ("i", "i16")])
    records, _ = _decode({"s": stream}, "s", FrameSynth(stream, seed=0).frames(0, 400))
    assert not np.allclose(records["a"], records["b"], atol=0.1)
    assert np.abs(records["i"]).max() > 10


def test_invalid_sim_specs_only_warn_and_fall_back() -> None:
    stream = _stream(
        [("loop_cntr", "u32"), ("x", "f32")],
        sim={"model": "robot", "fields": {"x": {"wave": "saw"}, "y": {"amp": "big"}}, "rate": 1},
    )
    problems = validate_stream("s", stream)
    assert {p.severity for p in problems} == {"warning"}
    assert len(problems) == 5
    records, _ = _decode({"s": stream}, "s", FrameSynth(stream, seed=0).frames(0, 50))
    assert records["x"].std() > 0  # the invalid spec fell back to a default wave


# --- PID motor model ---


def _pid_stream() -> StreamConfig:
    return copy.deepcopy(StreamConfigLoader(REPO_STREAMS).list_streams()["pid"])


def test_pid_model_tracks_the_target_within_limits() -> None:
    stream = _pid_stream()
    records, _ = _decode({"pid": stream}, "pid", FrameSynth(stream, seed=1).frames(0, 1600))

    settled = slice(300, 400)  # 1.5-2.0 s: target +0.3 rps, after the ramp and time constant
    for side in ("left", "right"):
        setpoint = records[f"{side}_setpoint"][settled]
        measurement = records[f"{side}_measurement"][settled]
        assert np.abs(measurement - setpoint).mean() < 0.15 * np.abs(setpoint).mean()
        assert np.abs(records[f"{side}_output"]).max() <= 100.0
        u_ff, u_pi = records[f"{side}_u_ff"], records[f"{side}_u_pi"]
        assert records[f"{side}_u_virtual"] == pytest.approx(u_ff + u_pi, abs=1e-4)
    assert records["left_target_setpoint"][900] == pytest.approx(-0.3)  # 4.5 s: reverse


def _commands() -> dict[str, CommandDef]:
    """The bundled streams.json commands (R5.2): pid_single (0x10) and pid_both (0x11)."""
    return StreamConfigLoader(REPO_STREAMS).commands


def _gains(*values: float, prefix: str = "") -> dict[str, float]:
    """use_ramp, use_pi, kp, ki, k1, k2, k3, k_aw, alpha, rps (the old argument order)."""
    names = ("use_ramp", "use_pi", "kp", "ki", "k1", "k2", "k3", "k_aw", "alpha", "rps")
    return {prefix + n: v for n, v in zip(names, values, strict=True)}


def _pid_single(motor_id: int, *gains: float) -> bytes:
    return encode_command(_commands()["pid_single"], {"motor_id": motor_id, **_gains(*gains)})


def test_pid_commands_change_the_simulated_response() -> None:
    stream = _pid_stream()
    synth = FrameSynth(stream, seed=1)
    commands = tuple(_commands().values())
    left_only = _pid_single(0, 0, 0, 1.0, 1.0, 26.5, 8.0, 0.0, 1.0, 0.5, 1.2)
    both = encode_command(
        _commands()["pid_both"],
        {
            **_gains(1, 1, 2.0, 3.0, 26.5, 8.0, 0.0, 1.0, 0.5, 0.8, prefix="left_"),
            **_gains(1, 1, 2.0, 3.0, 26.5, 8.0, 0, 1, 0.5, 0.9, prefix="right_"),
        },
    )
    parser = FrameParser()

    for packet_id, payload in parser.feed(left_only):
        assert synth.apply_command(packet_id, payload, commands)
    records, _ = _decode({"pid": stream}, "pid", synth.frames(0, 100))
    assert (records["left_u_pi"] == 0).all()  # use_pi = 0
    assert records["left_target_setpoint"][50] == pytest.approx(1.2)
    assert records["right_target_setpoint"][50] == pytest.approx(0.3 * 1.05)  # untouched

    for packet_id, payload in parser.feed(both):
        assert synth.apply_command(packet_id, payload, commands)
    assert synth.model is not None
    assert synth.model.gains("left").rps == pytest.approx(0.8)
    assert synth.model.gains("right").ki == pytest.approx(3.0)
    assert not synth.apply_command(0x42, b"\x00", commands)  # other packets are ignored
    assert not synth.apply_command(0x10, b"\x00", commands)  # wrong size for pid_single
    assert not synth.apply_command(0x10, left_only[5:-1], ())  # no layouts configured


def test_saturation_engages_anti_windup() -> None:
    stream = _pid_stream()
    synth = FrameSynth(stream, seed=1)
    packet = _pid_single(0, 0, 1, 5.0, 2.0, 26.5, 8.0, 0, 1.0, 0.5, 5.0)
    for packet_id, payload in FrameParser().feed(packet):
        synth.apply_command(packet_id, payload, tuple(_commands().values()))

    records, _ = _decode({"pid": stream}, "pid", synth.frames(0, 400))

    assert records["left_u_virtual"].max() > 100.0  # a 5 rps step (no ramp) needs > 100%
    assert records["left_u_sat"].max() == pytest.approx(100.0)
    assert records["left_aw_term"].min() < 0  # pulls the integrator back while saturated


# --- SimTransport pacing (fake clock) ---


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _transport(stream: StreamConfig, clock: _Clock) -> SimTransport:
    sim = SimTransport(stream, seed=0, clock=clock, sleep=clock.sleep)
    sim.open()
    return sim


def _counters(stream: StreamConfig, data: bytes) -> list[int]:
    records, _ = _decode({"s": stream}, "s", data)
    return [int(v) for v in records["loop_cntr"]]


def test_sim_transport_paces_frames_in_real_time() -> None:
    stream = _stream([("loop_cntr", "u32"), ("x", "f32")], time={"scale_s": 0.005})
    clock = _Clock()
    sim = _transport(stream, clock)

    first = sim.read(0.05)  # waits the minimum read interval, then frames 0..2 are due
    assert clock.sleeps == [pytest.approx(0.01)]
    assert _counters(stream, first) == [0, 1, 2]
    clock.now += 0.1
    assert _counters(stream, sim.read(0.05)) == list(range(3, 23))
    clock.sleeps.clear()
    assert sim.read(0.002) == b""  # nothing due within the timeout
    assert sum(clock.sleeps) == pytest.approx(0.002)


def test_sim_transport_skips_frames_it_fell_too_far_behind_on() -> None:
    stream = _stream([("loop_cntr", "u32"), ("x", "f32")], time={"scale_s": 0.001})
    clock = _Clock()
    sim = _transport(stream, clock)
    clock.now += 10.0  # 10000 frames due
    counters = _counters(stream, sim.read(0.05))
    assert len(counters) == 2000
    assert counters[-1] == 10000  # the newest are sent; the gap shows as lost frames


def test_sim_transport_retargets_and_continues_counting() -> None:
    a = _stream([("loop_cntr", "u32"), ("x", "f32")], time={"scale_s": 0.005})
    b = _stream([("loop_cntr", "u32"), ("y", "i16")], time={"scale_s": 0.02})
    clock = _Clock()
    sim = _transport(a, clock)
    clock.now += 0.05
    assert _counters(a, sim.read(0.05))[-1] == 10

    sim.set_stream(b)
    clock.now += 0.1  # 0.1 s at 20 ms per frame
    assert _counters(b, sim.read(0.05)) == [11, 12, 13, 14, 15, 16]


def test_sim_transport_lifecycle_and_commands() -> None:
    clock = _Clock()
    sim = _transport(_pid_stream(), clock)
    packet = _pid_single(1, 1, 1, 4.0, 0.5, 26.5, 8.0, 0, 1.0, 0.5, 0.7)
    sim.write(packet)
    assert sim.synth.model is not None and sim.synth.model.gains("right").kp == 0.1  # no layouts
    sim.set_commands(tuple(_commands().values()))
    sim.write(packet)
    assert sim.synth.model.gains("right").kp == 4.0
    assert sim.name == "VIRTUAL"
    sim.close()
    with pytest.raises(TransportError):
        sim.read(0.01)
    with pytest.raises(TransportError):
        sim.write(b"\x00")


# --- text profiles (R8.3) ---


def _text_stream() -> StreamConfig:
    return cast(
        StreamConfig,
        {
            "name": "IMU",
            "frame": {
                "pattern": "IMU,{ms},{ax},{n}",
                "fields": [
                    {"name": "ms", "type": "u32"},
                    {"name": "ax", "type": "f32"},
                    {"name": "n", "type": "i16"},
                ],
            },
            "time": {"field": "ms", "scale_s": 0.001, "step": 10},
            "sim": {"fields": {"ax": {"wave": "sine", "amp": 2.0}}},
        },
    )


def test_text_lines_print_the_pattern_and_decode_back() -> None:
    from core.protocol.text_line import TextLineDecoder

    stream = _text_stream()
    data = FrameSynth(stream, seed=0).lines(0, 250)  # 2.5 s at 10 ms per line
    lines = data.split(b"\r\n")
    assert lines[0] == b"# sim tick" and lines[1].startswith(b"IMU,0,")
    assert data.count(b"# sim tick") == 3  # at 0 s, 1 s and 2 s
    decoder = TextLineDecoder()
    decoder.configure({"s": stream})
    records = decoder.feed(data)["s"]
    assert records["ms"].tolist() == list(range(0, 2500, 10))
    assert np.abs(records["ax"]).max() == pytest.approx(2.0, abs=0.01)
    assert decoder.stats.lines_unmatched == 3
    assert decoder.stats.counter_gaps == decoder.stats.value_errors == 0


def test_a_text_sim_transport_prints_lines() -> None:
    clock = _Clock()
    sim = _transport(_text_stream(), clock)
    sim.set_text(True)
    clock.now += 0.05
    assert sim.read(0.05).startswith(b"# sim tick\r\nIMU,0,")
    sim.write(b"PID 1 2\n")  # no binary command parsing: answered as text (R8.5)


def test_a_text_sim_answers_each_line_it_is_sent() -> None:
    clock = _Clock()
    sim = _transport(_text_stream(), clock)
    sim.set_text(True)
    sim.write(b"PID 0 0.25\r")
    sim.write(b"\nSTATUS\n\n")
    assert sim.read(0.05) == b"ok: PID 0 0.25\r\nok: STATUS\r\n"
    assert sim.read(0.05).startswith(b"# sim tick")  # then the lines again
