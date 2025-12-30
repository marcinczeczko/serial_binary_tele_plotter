"""
Virtual Device Module.

This module provides a simulator that mimics the behavior of the physical MCU.
It generates synthetic telemetry data (sine waves, noise, PID terms) to allow
testing the GUI and data pipeline without a physical connection.
"""

import math
import random
from typing import Optional

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
        # Passing 'self' as the parent to QTimer is mandatory. It ensures that
        # when VirtualDevice moves to a thread, the Timer moves with it.
        # Otherwise, we get "Timers cannot be started from another thread" errors.
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._step)

        self._loop_cntr = 0
        self._period_s = 0.05
        self._motor_id = 0

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

    def _step(self):
        """
        Internal slot called by the timer.
        Generates one frame of synthetic math data representing a PID controller.
        """
        t = self._loop_cntr * self._period_s

        # 1. Generate artificial physics (Sine wave setpoint)
        setpoint = math.sin(t * 0.5)

        # 2. Simulate sensor noise
        noise = random.uniform(-0.05, 0.05)
        measurement = setpoint + noise

        # 3. Calculate derived values (PID logic simulation)
        error = setpoint - measurement

        # Mocking internal PID terms for visualization
        p_term = error * 20.0
        i_term = math.sin(t * 0.2) * 5.0

        # Output simulation with saturation
        raw_output = error * 30.0
        clamped_output = max(min(raw_output, 100), -100)

        # 4. Construct the frame dictionary
        frame = {
            "loopCntr": self._loop_cntr,
            "motor": self._motor_id,
            "setpoint": setpoint,
            "measurement": measurement,
            "measurementRaw": measurement + random.uniform(-0.02, 0.02),
            "error": error,
            "pTerm": p_term,
            "iTerm": i_term,
            "outputRaw": raw_output,
            "output": clamped_output,
        }

        # 5. Emit data to the Worker
        self.frame_generated.emit(frame)
        self._loop_cntr += 1
