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
        self._motor_id = 0
        self._stream_type = "pid"  # Default stream type

    def start(self, period_s: float, motor_id: int):
        """
        Starts the data generation loop.

        Args:
            period_s (float): Sampling period in seconds.
            motor_id (int): ID of the motor to simulate (0 or 1).
        """
        self._period_s = period_s
        self._motor_id = motor_id
        self._loop_cntr = 0  # Reset time on start

        # Interval expects milliseconds
        self.timer.start(int(period_s * 1000))

    def stop(self):
        """Stops the data generation loop."""
        self.timer.stop()

    def update_params(self, period_s: float, motor_id: int):
        """
        Updates simulation parameters on the fly without resetting the counter.

        Args:
            period_s (float): New sampling period in seconds.
            motor_id (int): New motor ID to simulate.
        """
        self._period_s = period_s
        self._motor_id = motor_id

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
        t = self._loop_cntr * self._period_s

        # --- FIX: Explicit Type Hinting ---
        # Definiujemy frame jako słownik string->cokolwiek, żeby Pylance
        # nie krzyczał, gdy dodajemy floaty do intów.
        frame: Dict[str, Any] = {
            "loopCntr": self._loop_cntr,
            "motor": self._motor_id,
        }

        if self._stream_type == "pid":
            # --- PID SIMULATION ---
            setpoint = math.sin(t * 0.5)
            # Add some noise
            noise = random.uniform(-0.05, 0.05)
            measurement = setpoint + noise

            # Calculate derived values (PID logic simulation)
            error = setpoint - measurement

            # Mocking internal PID terms
            p_term = error * 20.0
            i_term = math.sin(t * 0.2) * 5.0

            raw_output = error * 30.0
            clamped_output = max(min(raw_output, 100), -100)

            frame.update(
                {
                    "setpoint": setpoint,
                    "measurement": measurement,
                    "measurementRaw": measurement + random.uniform(-0.02, 0.02),
                    "error": error,
                    "pTerm": p_term,
                    "iTerm": i_term,
                    "outputRaw": raw_output,
                    "output": clamped_output,
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
