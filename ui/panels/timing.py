"""
Time Configuration Panel Module.

This module provides the `TimeConfigPanel` widget, which controls the temporal
parameters of the data acquisition system:
1. **Sampling Period**: How often data is read/generated.
2. **Buffer Size**: How much history is kept in memory (and shown on the plot).
"""

from __future__ import annotations

from PyQt6 import QtCore, QtWidgets


class TimeConfigPanel(QtWidgets.QGroupBox):
    """
    A group box for configuring time window parameters.

    It bridges the UI inputs to the TelemetryEngine, allowing dynamic resizing
    of data buffers and adjustment of the acquisition loop speed.

    Attributes:
        time_config_changed (pyqtSignal): Emitted when period or sample count changes.
            Payload: (period_ms: float, max_samples: int).
    """

    time_config_changed = QtCore.pyqtSignal(float, int)

    def __init__(self) -> None:
        """Initializes the Time Config panel layout and widgets."""
        super().__init__("Time Window")

        layout = QtWidgets.QGridLayout(self)
        layout.setSpacing(8)

        # --- Sample Period Input ---
        # Controls the dt (delta time) between points.
        self.period_sb = QtWidgets.QDoubleSpinBox()
        self.period_sb.setRange(1.0, 1000.0)
        self.period_sb.setValue(5.0)  # Default: 5ms (200Hz)
        self.period_sb.setSuffix(" ms")
        self.period_sb.setSingleStep(1.0)
        self.period_sb.setToolTip(
            "Time interval between data points.\n"
            "Lower value = Higher frequency (more CPU usage)."
        )

        # --- Sample Count Input ---
        # Controls the size of the Deque (Ring Buffer).
        self.samples_sb = QtWidgets.QSpinBox()
        self.samples_sb.setRange(10, 100000)
        self.samples_sb.setValue(2000)  # Default: 2000 points history
        self.samples_sb.setSingleStep(100)
        self.samples_sb.setToolTip(
            "Number of data points to keep in history.\n" "Total Time Window = Period * Samples."
        )

        # --- Signal Wiring ---
        # Emit signal immediately on change to update Engine/Plot in real-time
        self.period_sb.valueChanged.connect(self._emit_config)
        self.samples_sb.valueChanged.connect(self._emit_config)

        # --- Layout Assembly ---
        layout.addWidget(QtWidgets.QLabel("Period:"), 0, 0)
        layout.addWidget(self.period_sb, 0, 1)

        layout.addWidget(QtWidgets.QLabel("Samples:"), 1, 0)
        layout.addWidget(self.samples_sb, 1, 1)

    def _emit_config(self) -> None:
        """
        Slot called when spinboxes change.
        Emits the current configuration values to the container.
        """
        self.time_config_changed.emit(self.period_sb.value(), self.samples_sb.value())

    # --- Public Accessors (for MainWindow Initialization) ---

    def get_period(self) -> float:
        """Returns the current sampling period in milliseconds."""
        return self.period_sb.value()

    def get_samples(self) -> int:
        """Returns the current sample buffer size."""
        return self.samples_sb.value()
