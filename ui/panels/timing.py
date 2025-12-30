"""
Time Configuration Panel Module.

This module provides controls for setting the sampling rate and the size of the
history buffer (window size) for the telemetry data.
"""

from PyQt6 import QtCore, QtWidgets


class TimeConfigPanel(QtWidgets.QGroupBox):
    """
    A group box for configuring time window parameters.

    Attributes:
        time_config_changed (pyqtSignal): Emitted when period or sample count changes.
            Payload: (period_ms: float, max_samples: int)
    """

    time_config_changed = QtCore.pyqtSignal(float, int)

    def __init__(self):
        """Initializes the Time Config panel."""
        super().__init__("Time Window")

        layout = QtWidgets.QGridLayout(self)
        layout.setSpacing(8)

        # Sample Period Input
        self.period_sb = QtWidgets.QDoubleSpinBox()
        self.period_sb.setRange(1.0, 1000.0)
        self.period_sb.setValue(5.0)  # Default: 5ms
        self.period_sb.setSuffix(" ms")
        self.period_sb.setSingleStep(1.0)

        # Sample Count Input
        self.samples_sb = QtWidgets.QSpinBox()
        self.samples_sb.setRange(10, 100000)
        self.samples_sb.setValue(2000)  # Default: 2000 samples
        self.samples_sb.setSingleStep(100)

        # Connect signals
        self.period_sb.valueChanged.connect(self._emit_config)
        self.samples_sb.valueChanged.connect(self._emit_config)

        # Layout
        layout.addWidget(QtWidgets.QLabel("Period:"), 0, 0)
        layout.addWidget(self.period_sb, 0, 1)

        layout.addWidget(QtWidgets.QLabel("Samples:"), 1, 0)
        layout.addWidget(self.samples_sb, 1, 1)

    def _emit_config(self):
        """Emits the current configuration values."""
        self.time_config_changed.emit(self.period_sb.value(), self.samples_sb.value())

    # --- Public Accessors ---

    def get_period(self) -> float:
        """Returns the current sampling period in milliseconds."""
        return self.period_sb.value()

    def get_samples(self) -> int:
        """Returns the current sample buffer size."""
        return self.samples_sb.value()
