"""
Frame synthesis for the simulator (R2.7, review finding A6).

`FrameSynth` builds real protocol frames (header, CRC, packed payload) for any stream in
streams.json, so the simulator exercises the same parser, router and store as hardware.
For a text profile it prints the stream's `frame.pattern` lines instead (R8.3), with
`\r\n` endings like Arduino's `println`, and a `# sim tick` line once a second that no
pattern matches, so the unmatched-line counter has something to count.
What each field carries comes from config, never from the stream's name:

    "sim": {
        "model": "pid_motor",
        "fields": {"acc_z": {"wave": "const", "offset": 1.0, "noise": 0.02}}
    }

- The time field and `loop_cntr` count frames (wrapping like the MCU's integers).
- `model` fills the fields it knows (see `pid_motor.PidMotorModel`).
- `fields` gives a waveform per field.
- Every other field gets a default waveform, distinct per field.

Pure Python + numpy, no Qt.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from core.acquisition.timebase import time_base_config
from core.protocol.commands import CommandDef, decode_command
from core.protocol.constants import LOOP_CNTR_NAME, MAGIC_0, MAGIC_1, STRUCT_TYPE_MAP
from core.protocol.crc import calculate_crc8
from core.protocol.record_decoder import frame_dtype
from core.protocol.text_line import format_line, parse_pattern, pattern_of
from core.simulation.pid_motor import PidMotorModel
from core.types import StreamConfig

WAVES = ("sine", "step", "noise", "const", "counter")
WAVE_KEYS = ("wave", "amp", "freq_hz", "offset", "phase_deg", "noise")
SIM_MODELS = ("pid_motor",)
SIM_KEYS = ("model", "fields")
SIM_TICK_LINE = b"# sim tick\r\n"


@dataclass(frozen=True)
class Wave:
    """
    One field's signal: `offset + amp * shape(2*pi*freq_hz*t + phase)`, plus Gaussian noise
    with standard deviation `noise`.

    Shapes: `sine`; `step` (a square wave between offset - amp and offset + amp); `noise`
    (offset + noise only); `const` (offset); `counter` (offset + amp per frame).
    """

    wave: str = "sine"
    amp: float = 1.0
    freq_hz: float = 0.5
    offset: float = 0.0
    phase_deg: float = 0.0
    noise: float = 0.0

    def evaluate(self, k: np.ndarray, t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        angle = 2 * math.pi * self.freq_hz * t + math.radians(self.phase_deg)
        if self.wave == "sine":
            values = self.offset + self.amp * np.sin(angle)
        elif self.wave == "step":
            values = self.offset + self.amp * np.where(np.sin(angle) >= 0, 1.0, -1.0)
        elif self.wave == "counter":
            values = self.offset + self.amp * k.astype(np.float64)
        else:  # const, noise
            values = np.full(len(t), self.offset)
        if self.noise:
            values = values + rng.normal(0.0, self.noise, len(t))
        return values


def wave_from_config(spec: Any) -> Wave | None:
    """
    Builds a `Wave` from a `sim.fields` entry. Invalid entries give None, and invalid
    parameters are skipped: validation only warns about them, so they must not break VIRTUAL.
    """
    if not isinstance(spec, dict) or spec.get("wave", "sine") not in WAVES:
        return None
    params = {
        k: float(v)
        for k, v in spec.items()
        if k in WAVE_KEYS and k != "wave" and isinstance(v, int | float) and not isinstance(v, bool)
    }
    return Wave(wave=str(spec.get("wave", "sine")), **params)


def _default_wave(index: int, ftype: str) -> Wave:
    """Distinct, plausible, never-flat defaults: fields don't look alike or stay at zero."""
    code = STRUCT_TYPE_MAP[ftype][0]
    freq = 0.1 * (1 + index % 5)
    phase = 37.0 * index
    if code in "fd":
        return Wave("sine", amp=1.0, freq_hz=freq, phase_deg=phase, noise=0.02)
    lo, hi = _int_range(ftype)
    amp = min(100.0, (hi - lo) / 4)
    return Wave("sine", amp=amp, freq_hz=freq, offset=lo + amp if lo == 0 else 0.0)


def _int_range(ftype: str) -> tuple[float, float]:
    code = STRUCT_TYPE_MAP[ftype][0]
    bits = 8 * struct.calcsize("<" + code)
    if code.isupper():  # unsigned
        return 0.0, 2.0**bits - 1
    return -(2.0 ** (bits - 1)), 2.0 ** (bits - 1) - 1


def _to_field(values: np.ndarray, ftype: str, wrap: bool) -> np.ndarray:
    """Converts floats to what the field can hold: counters wrap, other integers clip."""
    code = STRUCT_TYPE_MAP[ftype][0]
    if code in "fd":
        return values
    lo, hi = _int_range(ftype)
    out: np.ndarray = (
        np.mod(values - lo, hi - lo + 1) + lo if wrap else np.clip(np.rint(values), lo, hi)
    )
    return out


class FrameSynth:
    """Generates the frames of one stream: frame number k is at time k x period."""

    def __init__(self, stream: StreamConfig, seed: int | None = None) -> None:
        frame = stream["frame"]
        self.stream = stream
        self._fields = [(f["name"], f["type"]) for f in frame["fields"]]
        self._dtype = frame_dtype(frame.get("endianness", "little"), frame["fields"])
        # A text stream has no stream_id; its header is never used.
        header = bytes([MAGIC_0, MAGIC_1, frame.get("stream_id", 0), self._dtype.itemsize])
        self._header = header + bytes([calculate_crc8(header)])
        pattern = pattern_of(stream)
        self._pattern = parse_pattern(pattern) if pattern is not None else None
        time_cfg = time_base_config(stream)
        self._time_field = time_cfg.field
        self._time_step = time_cfg.step
        self.period_s = time_cfg.period_s
        self._rng = np.random.default_rng(seed)

        raw_sim: Any = stream.get("sim")
        sim: dict[str, Any] = raw_sim if isinstance(raw_sim, dict) else {}
        self.model = PidMotorModel(self._rng) if sim.get("model") == "pid_motor" else None
        model_fields = self.model.field_names() if self.model else set()
        raw_specs: Any = sim.get("fields")
        specs: dict[str, Any] = raw_specs if isinstance(raw_specs, dict) else {}
        self._waves: dict[str, Wave] = {}
        free = 0
        for name, ftype in self._fields:
            wave = wave_from_config(specs.get(name))
            if wave is not None:
                self._waves[name] = wave
            elif name in (self._time_field, LOOP_CNTR_NAME):
                continue  # counts frames
            elif name not in model_fields:
                self._waves[name] = _default_wave(free, ftype)
                free += 1
        self._model_fields = [(n, t) for n, t in self._fields if n in model_fields]

    def frames(self, k0: int, n: int) -> bytes:
        """Encodes frames k0 .. k0 + n - 1 (consecutive calls continue the models)."""
        if n <= 0:
            return b""
        blob = self._records(k0, n).tobytes()
        size = self._dtype.itemsize
        out = bytearray()
        for i in range(n):
            payload = blob[i * size : (i + 1) * size]
            out += self._header
            out += payload
            out.append(calculate_crc8(payload))
        return bytes(out)

    def lines(self, k0: int, n: int) -> bytes:
        """
        Prints frames k0 .. k0 + n - 1 as the stream's pattern lines (a text profile),
        with a `# sim tick` line before every frame that starts a second.
        """
        if n <= 0 or self._pattern is None:
            return b""
        records = self._records(k0, n)
        names = [name for name, _ in self._fields]
        per_second = max(1, round(1.0 / max(self.period_s, 1e-6)))
        out = bytearray()
        for k, row in enumerate(records.tolist(), start=k0):
            if k % per_second == 0:
                out += SIM_TICK_LINE
            line = format_line(self._pattern, dict(zip(names, row, strict=True)))
            out += line.encode("ascii") + b"\r\n"
        return bytes(out)

    def _records(self, k0: int, n: int) -> np.ndarray:
        k = np.arange(k0, k0 + n, dtype=np.int64)
        t = k * self.period_s
        records = np.zeros(n, dtype=self._dtype)
        for name, ftype in self._fields:
            wave = self._waves.get(name)
            if wave is not None:
                records[name] = _to_field(wave.evaluate(k, t, self._rng), ftype, wrap=False)
            elif name == self._time_field:
                records[name] = _to_field(k * self._time_step, ftype, wrap=True)
            elif name == LOOP_CNTR_NAME:
                records[name] = _to_field(k.astype(np.float64), ftype, wrap=True)
        if self.model is not None and self._model_fields:
            steps = [self.model.step(float(ti), self.period_s) for ti in t.tolist()]
            for name, ftype in self._model_fields:
                column = np.array([s[name] for s in steps], dtype=np.float64)
                records[name] = _to_field(column, ftype, wrap=False)
        return records

    def apply_command(self, packet_id: int, payload: bytes, commands: Sequence[CommandDef]) -> bool:
        """
        Applies a received command to the model, as the firmware would: decoded with the
        first configured command of that ID whose payload size matches. Returns True if the
        model used it.
        """
        if self.model is None:
            return False
        for command in commands:
            if command.packet_id != packet_id:
                continue
            values = decode_command(command, payload)
            if values is not None:
                return self.model.apply_command(values)
        return False
