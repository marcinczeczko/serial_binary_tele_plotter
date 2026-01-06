"""
Virtual Device Module.

This module provides a simulator that mimics the behavior of the physical MCU.
It generates synthetic telemetry data (sine waves, noise, PID terms) to allow
testing the GUI and data pipeline without a physical connection.
"""

import math
import random
from typing import Any, Dict, Optional

from PyQt6 import QtCore

from core.protocol.constants import LOOP_CNTR_NAME


class VirtualDevice(QtCore.QObject):
    """
    Simulates a hardware device by generating fake telemetry data on a timer.

    This class is designed to be a drop-in replacement for the Serial Port logic.
    It emits dictionaries that structurally match the output of the FrameDecoder.

    Attributes:
        frame_generated (pyqtSignal): Signal emitted every simulation step.
                                      Payload: dict (decoded frame structure).
    """

    # Signal emitting the simulated data frame (dictionary)
    frame_generated = QtCore.pyqtSignal(dict)

    def __init__(self, parent: Optional[QtCore.QObject] = None):
        """
        Initializes the Virtual Device.

        Args:
            parent (QObject, optional): The parent object. Crucial for Qt's
                thread affinity system. Passing 'self' from the Worker allows
                this object to move to the worker thread automatically.
        """
        super().__init__(parent)

        # Initialize the internal timer.
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._step)

        self._loop_cntr = 0
        self._period_s = 0.05
        self._stream_type = "pid"  # Default stream type
        self._pid_sim = {
            "left": {
                "measurement": 0.0,
                "integral": 0.0,
                "velocity": 0.0,
                "ramp_setpoint": 0.0,
            },
            "right": {
                "measurement": 0.0,
                "integral": 0.0,
                "velocity": 0.0,
                "ramp_setpoint": 0.0,
            },
        }

    def start(self, period_s: float):
        """
        Starts the data generation loop.

        Args:
            period_s (float): Sampling period in seconds.
        """
        self._period_s = period_s
        self._loop_cntr = 0  # Reset time on start

        # Interval expects milliseconds
        self.timer.start(int(period_s * 1000))

    def stop(self):
        """Stops the data generation loop."""
        self.timer.stop()

    def update_params(self, period_s: float):
        """
        Updates simulation parameters on the fly without resetting the counter.

        Args:
            period_s (float): New sampling period in seconds.
        """
        self._period_s = period_s

        # If running, update the timer interval immediately
        if self.timer.isActive():
            self.timer.setInterval(int(period_s * 1000))

    def configure_stream(self, stream_type: str):
        """
        Sets the type of data to simulate based on config name.
        Example: "PID Telemetry" or "IMU 6-Axis"
        """
        name = stream_type.lower()
        if "imu" in name:
            self._stream_type = "imu"
        elif "control" in name:
            self._stream_type = "control"
        else:
            self._stream_type = "pid"

    def _step(self):
        """
        Internal slot called by the timer.
        Generates one frame of synthetic data based on the selected stream type.
        """

        def ramp(current, target, rate, dt):
            delta = target - current
            max_step = rate * dt
            if abs(delta) <= max_step:
                return target
            return current + math.copysign(max_step, delta)

        t = self._loop_cntr * self._period_s

        # --- FIX: Explicit Type Hinting ---
        # Definiujemy frame jako słownik string->cokolwiek, żeby Pylance
        # nie krzyczał, gdy dodajemy floaty do intów.
        frame: Dict[str, Any] = {
            LOOP_CNTR_NAME: self._loop_cntr,
        }

        if self._stream_type == "pid":
            dt = 0.005  # 5ms
            ticks_per_rev = 3800
            ramp_rate = 1.5  # RPS/s – jak w MCU

            base_target = math.sin(t * 0.5) * 1.0

            for side in ("left", "right"):
                sim = self._pid_sim[side]

                # --- external command ---
                target_setpoint = base_target + (0.05 if side == "right" else 0.0)

                # --- RAMP (this is what PID sees) ---
                sim["ramp_setpoint"] = ramp(
                    sim["ramp_setpoint"],
                    target_setpoint,
                    ramp_rate,
                    dt,
                )
                setpoint = sim["ramp_setpoint"]

                # --- plant ---
                tau = 0.15
                sim["velocity"] += (setpoint - sim["velocity"]) * (dt / tau)

                delta_ticks = int(sim["velocity"] * ticks_per_rev * dt)

                measurement_raw = sim["velocity"] + random.uniform(-0.02, 0.02)
                measurement = sim["measurement"] * 0.7 + measurement_raw * 0.3
                sim["measurement"] = measurement

                # --- PI ---
                error = setpoint - measurement

                kp, ki, kff = 20.0, 10.0, 30.0

                sim["integral"] += error * dt
                sim["integral"] = max(min(sim["integral"], 50.0), -50.0)

                p_term = kp * error
                i_term = ki * sim["integral"]
                ff_term = kff * setpoint

                raw_output = p_term + i_term + ff_term
                output = max(min(raw_output, 100.0), -100.0)

                if raw_output != output:
                    sim["integral"] -= error * dt

                frame.update(
                    {
                        f"{side}_target_setpoint": target_setpoint,
                        f"{side}_setpoint": setpoint,
                        f"{side}_measurement": measurement,
                        f"{side}_measurementRaw": measurement_raw,
                        f"{side}_error": error,
                        f"{side}_integral": sim["integral"],
                        f"{side}_pTerm": p_term,
                        f"{side}_iTerm": i_term,
                        f"{side}_outputRaw": raw_output,
                        f"{side}_output": output,
                        f"{side}_delta_ticks": delta_ticks,
                    }
                )

        elif self._stream_type == "imu":
            # --- IMU SIMULATION ---
            # Simulate generic movements
            # Accel Z ~ 1g (gravity), X/Y noise around 0

            frame.update(
                {
                    "acc_x": math.sin(t * 2.0) * 0.5 + random.uniform(-0.1, 0.1),
                    "acc_y": math.cos(t * 2.0) * 0.5 + random.uniform(-0.1, 0.1),
                    "acc_z": 1.0 + random.uniform(-0.05, 0.05),
                    "gyro_x": random.uniform(-5, 5),
                    "gyro_y": random.uniform(-5, 5),
                    "gyro_z": math.sin(t) * 50.0,
                }
            )
        elif self._stream_type == "control":
            # --- CONTROL LOOP SIMULATION ---
            sp_l = math.sin(t * 1.0)
            sp_r = math.cos(t * 1.0)

            frame.update(
                {
                    "setpointL": sp_l,
                    "setpointR": sp_r,
                    "rampSetpointL": sp_l * 0.9,
                    "rampSetpointR": sp_r * 0.9,
                    "measuredRpsL": sp_l * 0.9 + random.uniform(-0.05, 0.05),
                    "measuredRpsR": sp_r * 0.9 + random.uniform(-0.05, 0.05),
                    "deltaLticks": random.uniform(-1, 1),
                    "deltaRticks": random.uniform(-1, 1),
                }
            )

        # Emit data to the Worker
        self.frame_generated.emit(frame)
        self._loop_cntr += 1
