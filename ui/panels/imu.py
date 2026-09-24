"""
IMU Calibration Panel.

The firmware protocol defines no IMU command packet yet, so these buttons are shown
disabled rather than pretending to do something (C7). Config-defined commands
(roadmap R5.2) are the way to add them without hard-coding a wire format here.
"""

from __future__ import annotations

from PyQt6 import QtWidgets

NOT_AVAILABLE = (
    "Not available: the wire protocol has no IMU command packet yet.\n"
    "See roadmap R5.2 (commands defined in streams.json)."
)


class ImuCalibrationPanel(QtWidgets.QGroupBox):
    def __init__(self) -> None:
        super().__init__("IMU Control")

        layout = QtWidgets.QVBoxLayout(self)

        self.btn_zero_gyro = QtWidgets.QPushButton("Zero Gyroscope")
        self.btn_acc_calib = QtWidgets.QPushButton("Calibrate Accelerometer")
        for btn in (self.btn_zero_gyro, self.btn_acc_calib):
            btn.setEnabled(False)
            btn.setToolTip(NOT_AVAILABLE)
            layout.addWidget(btn)
