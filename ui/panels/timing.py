"""
Time Configuration Panel Module.

`TimeConfigPanel` controls (shown from the "Period" and "History" buttons next to the
stream tabs, R6.1):
1. **Period** of the stream shown: the time between two of its frames. It comes from the
   stream's `time` block in streams.json (R2.5). Editing it here overrides it for this
   session only. Set it permanently in the Configuration tab.
2. **Samples**: how much history each stream's buffer keeps (shared by all streams).
"""

from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from ui.common.numbers import ScopeDoubleSpinBox

_OVERRIDE_STYLE = "QDoubleSpinBox { color: #FFB000; }"


class TimeConfigPanel(QtWidgets.QWidget):
    """
    Attributes:
        period_changed (pyqtSignal): the user edited the shown stream's period (ms).
        samples_changed (pyqtSignal): the user edited the buffer size (samples).
    """

    period_changed = QtCore.pyqtSignal(float)
    samples_changed = QtCore.pyqtSignal(int)

    def __init__(self) -> None:
        super().__init__()

        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self._configured_ms: float | None = None

        # --- Period of the shown stream ---
        self.period_sb = ScopeDoubleSpinBox()
        self.period_sb.setDecimals(3)
        self.period_sb.setRange(0.001, 60_000.0)
        self.period_sb.setValue(5.0)
        self.period_sb.setSuffix(" ms")
        self.period_sb.setSingleStep(1.0)

        # --- Sample Count Input ---
        self.samples_sb = QtWidgets.QSpinBox()
        self.samples_sb.setRange(10, 100000)
        self.samples_sb.setValue(2000)  # Default: 2000 points history
        self.samples_sb.setSingleStep(100)
        self.samples_sb.setToolTip(
            "Number of data points to keep in history.\nTotal Time Window = Period * Samples."
        )

        # --- Signal Wiring ---
        # Without keyboard tracking, typed values are applied on Enter or focus-out, not on
        # every keystroke (typing "100000" used to reallocate the buffers six times, C12).
        # Arrow and wheel steps still apply immediately.
        self.period_sb.setKeyboardTracking(False)
        self.samples_sb.setKeyboardTracking(False)
        self.period_sb.valueChanged.connect(self._on_period_edited)
        self.samples_sb.valueChanged.connect(self.samples_changed)

        # --- Layout Assembly ---
        layout.addWidget(QtWidgets.QLabel("Period:"), 0, 0)
        layout.addWidget(self.period_sb, 0, 1)

        layout.addWidget(QtWidgets.QLabel("Samples:"), 1, 0)
        layout.addWidget(self.samples_sb, 1, 1)
        self._update_period_hint()

    def show_period(self, period_ms: float, configured_ms: float) -> None:
        """Shows a stream's period (and its streams.json value) without emitting a change."""
        self._configured_ms = configured_ms
        self.period_sb.blockSignals(True)
        self.period_sb.setValue(period_ms)
        self.period_sb.blockSignals(False)
        self._update_period_hint()

    def _on_period_edited(self, value: float) -> None:
        self._update_period_hint()
        self.period_changed.emit(value)

    def is_overridden(self) -> bool:
        configured = self._configured_ms
        return configured is not None and abs(self.period_sb.value() - configured) > 1e-9

    def _update_period_hint(self) -> None:
        configured = self._configured_ms
        tip = (
            "Time between two frames of the shown stream; the X axis is its time field "
            "(loop_cntr by default) times this period."
        )
        if configured is not None:
            tip += f"\nstreams.json: {configured:g} ms (time.scale_s x time.step)."
        if self.is_overridden():
            tip += "\nOverridden for this session: set it in the Configuration tab to keep it."
        self.period_sb.setToolTip(tip)
        self.period_sb.setStyleSheet(_OVERRIDE_STYLE if self.is_overridden() else "")

    # --- Public Accessors ---

    def get_period(self) -> float:
        """Returns the shown stream's period in milliseconds."""
        return self.period_sb.value()

    def get_samples(self) -> int:
        """Returns the current sample buffer size."""
        return self.samples_sb.value()
