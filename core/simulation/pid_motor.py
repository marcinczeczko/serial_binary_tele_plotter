"""
A small closed-loop model of two DC motors under feedforward + PI speed control (R2.7).

It produces the quantities a motor-control firmware typically streams (setpoint,
measurement, the PI terms, saturation, anti-windup, PWM), so a `pid` stream shows
plausible, correlated data on VIRTUAL. It also applies received commands, so gains sent
from a control panel visibly change the response.

Frame fields are matched by name: `{side}_{quantity}`, where side is `left` or `right` and
quantity is one of `QUANTITIES`. Fields the model doesn't know get a default waveform.

Commands are matched by field name, not by packet ID (R5.2): any command whose fields
name gains (`GAIN_NAMES`) updates them, for the motor in its `motor_id` field (0 left,
1 right), or per side with `left_`/`right_` prefixes. So the bundled `pid_single` and
`pid_both` commands work, and so would a command carrying only `left_kp`. In the model:
- `k1` is the feedforward gain in %/rps.
- `k2` is the static-friction offset in %.
- `k3` is ignored.
- `alpha` is the measurement filter coefficient (0..1).
- `rps` is the amplitude of the target profile.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from typing import Any

import numpy as np

SIDES = ("left", "right")
QUANTITIES = (
    "target_setpoint",
    "setpoint",
    "measurement",
    "measurementRaw",
    "error",
    "integral",
    "u_ff",
    "u_pi",
    "u_virtual",
    "u_sat",
    "pTerm",
    "iTerm",
    "aw_term",
    "outputRaw",
    "output",
    "pwm_cmd",
    "delta_ticks",
)

OUTPUT_LIMIT = 100.0  # % duty
RAMP_RATE_RPS_S = 1.5
PLANT_TAU_S = 0.15
PLANT_GAIN_ERROR = 1.1  # the real motor is 10% weaker than the feedforward assumes
TICKS_PER_REV = 3800
PROFILE_PERIOD_S = 8.0  # target: +rps, 0, -rps, 0, 2 s each
NOISE_RPS = 0.02


@dataclass
class Gains:
    """Defaults match the PID panel's defaults, so sending them changes nothing."""

    kp: float = 0.1
    ki: float = 0.02
    k1: float = 26.5
    k2: float = 8.0
    k3: float = 5.0
    k_aw: float = 1.0
    alpha: float = 0.2
    rps: float = 0.3
    use_ramp: bool = True
    use_pi: bool = True


GAIN_NAMES = tuple(f.name for f in fields(Gains))


@dataclass
class _Motor:
    gains: Gains = field(default_factory=Gains)
    ramp_setpoint: float = 0.0
    velocity: float = 0.0
    measurement: float = 0.0
    integral: float = 0.0


class PidMotorModel:
    def __init__(self, rng: np.random.Generator) -> None:
        self._rng = rng
        self._motors = {side: _Motor() for side in SIDES}

    @staticmethod
    def field_names() -> set[str]:
        return {f"{side}_{q}" for side in SIDES for q in QUANTITIES}

    def gains(self, side: str) -> Gains:
        return self._motors[side].gains

    def set_gains(self, side: str, gains: Gains) -> None:
        self._motors[side].gains = gains

    def apply_command(self, values: Mapping[str, float | int | bool]) -> bool:
        """Updates the gains a decoded command names; True if it named any."""
        used = False
        motor_id = values.get("motor_id")
        if motor_id is not None and 0 <= int(motor_id) < len(SIDES):
            used |= self._update(SIDES[int(motor_id)], values, "")
        for side in SIDES:
            used |= self._update(side, values, f"{side}_")
        return used

    def _update(self, side: str, values: Mapping[str, float | int | bool], prefix: str) -> bool:
        changes: dict[str, Any] = {}
        for name in GAIN_NAMES:
            if prefix + name in values:
                value = values[prefix + name]
                changes[name] = bool(value) if name.startswith("use_") else float(value)
        if changes:
            self._motors[side].gains = replace(self._motors[side].gains, **changes)
        return bool(changes)

    def step(self, t: float, dt: float) -> dict[str, float]:
        """Advances both motors by one control period; returns every quantity by field name."""
        out: dict[str, float] = {}
        for i, side in enumerate(SIDES):
            out.update(self._step_motor(side, self._motors[side], t, dt, i))
        return out

    def _step_motor(self, side: str, m: _Motor, t: float, dt: float, i: int) -> dict[str, float]:
        g = m.gains
        phase = int((t % PROFILE_PERIOD_S) // (PROFILE_PERIOD_S / 4))
        target = (g.rps, 0.0, -g.rps, 0.0)[phase] * (1.0 + 0.05 * i)  # right slightly off

        if g.use_ramp:
            delta = target - m.ramp_setpoint
            m.ramp_setpoint += math.copysign(min(abs(delta), RAMP_RATE_RPS_S * dt), delta)
        else:
            m.ramp_setpoint = target
        setpoint = m.ramp_setpoint

        measurement_raw = m.velocity + float(self._rng.normal(0.0, NOISE_RPS))
        alpha = min(max(g.alpha, 0.0), 1.0)
        m.measurement = alpha * measurement_raw + (1.0 - alpha) * m.measurement
        error = setpoint - m.measurement

        u_ff = g.k1 * setpoint + (math.copysign(g.k2, setpoint) if setpoint else 0.0)
        p_term = g.kp * error if g.use_pi else 0.0
        i_term = g.ki * m.integral if g.use_pi else 0.0
        u_pi = p_term + i_term
        u_virtual = u_ff + u_pi
        u_sat = min(max(u_virtual, -OUTPUT_LIMIT), OUTPUT_LIMIT)
        aw_term = g.k_aw * (u_sat - u_virtual)
        if g.use_pi:
            m.integral += (error + aw_term) * dt  # back-calculation anti-windup

        # Plant: first-order speed response to duty, minus static friction.
        drive = max(abs(u_sat) - g.k2, 0.0) * (1.0 if u_sat >= 0 else -1.0)
        steady_rps = drive / (g.k1 * PLANT_GAIN_ERROR) if g.k1 else 0.0
        m.velocity += (steady_rps - m.velocity) * min(dt / PLANT_TAU_S, 1.0)

        values = {
            "target_setpoint": target,
            "setpoint": setpoint,
            "measurement": m.measurement,
            "measurementRaw": measurement_raw,
            "error": error,
            "integral": m.integral,
            "u_ff": u_ff,
            "u_pi": u_pi,
            "u_virtual": u_virtual,
            "u_sat": u_sat,
            "pTerm": p_term,
            "iTerm": i_term,
            "aw_term": aw_term,
            "outputRaw": u_virtual,
            "output": u_sat,
            "pwm_cmd": u_sat * 10.0,  # a 0..1000 timer compare value
            "delta_ticks": m.velocity * TICKS_PER_REV * dt,
        }
        return {f"{side}_{q}": v for q, v in values.items()}
