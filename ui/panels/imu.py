"""
IMU Calibration Panel.
"""

from __future__ import annotations

from PyQt6 import QtCore, QtWidgets


class ImuCalibrationPanel(QtWidgets.QGroupBox):
    # Definiujemy sygnał, który ten panel wysyła w świat
    # Np. (command_id: int)
    calibration_requested = QtCore.pyqtSignal(int)

    def __init__(self) -> None:
        super().__init__("IMU Control")

        layout = QtWidgets.QVBoxLayout(self)

        self.btn_zero_gyro = QtWidgets.QPushButton("Zero Gyroscope")
        self.btn_acc_calib = QtWidgets.QPushButton("Calibrate Accelerometer")

        layout.addWidget(self.btn_zero_gyro)
        layout.addWidget(self.btn_acc_calib)

        # Podłączamy przyciski
        self.btn_zero_gyro.clicked.connect(lambda: self.calibration_requested.emit(1))
        self.btn_acc_calib.clicked.connect(lambda: self.calibration_requested.emit(2))
