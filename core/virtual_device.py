import math
import random

from PyQt6 import QtCore


class VirtualDevice(QtCore.QObject):
    """
    Simulates a hardware device by generating fake telemetry data on a timer.
    """

    # Signal emitting the simulated data frame (dictionary)
    frame_generated = QtCore.pyqtSignal(dict)

    # --- ZMIANA: Dodaj argument parent=None ---
    def __init__(self, parent=None):
        super().__init__(parent)  # Przekaż parent do QObject

        # Timer też musi mieć rodzica (self), żeby podążał za VirtualDevice
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._step)
        self._loop_cntr = 0
        self._period_s = 0.05
        self._motor_id = 0

    def start(self, period_s: float, motor_id: int):
        self._period_s = period_s
        self._motor_id = motor_id
        self._loop_cntr = 0
        self.timer.start(int(period_s * 1000))

    def stop(self):
        self.timer.stop()

    def update_params(self, period_s: float, motor_id: int):
        self._period_s = period_s
        self._motor_id = motor_id
        if self.timer.isActive():
            self.timer.setInterval(int(period_s * 1000))

    def _step(self):
        t = self._loop_cntr * self._period_s

        # Simulation math
        setpoint = math.sin(t * 0.5)
        measurement = setpoint + random.uniform(-0.05, 0.05)
        error = setpoint - measurement

        frame = {
            "loopCntr": self._loop_cntr,
            "motor": self._motor_id,
            "setpoint": setpoint,
            "measurement": measurement,
            "measurementRaw": measurement + random.uniform(-0.02, 0.02),
            "error": error,
            "pTerm": error * 20.0,
            "iTerm": math.sin(t * 0.2) * 5.0,
            "outputRaw": error * 30.0,
            "output": max(min(error * 30.0, 100), -100),
        }

        self.frame_generated.emit(frame)
        self._loop_cntr += 1
