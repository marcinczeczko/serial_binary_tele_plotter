"""
Numbers as a scope shows them: a dot decimal separator whatever the system locale, and only
the digits that matter (`0.12`, `26.5`, not `0,1200`, `26,5000`). ADR-0012 decision 5.
"""

from __future__ import annotations

import math

from PyQt6 import QtCore, QtGui, QtWidgets


def format_number(value: float, decimals: int = 3, sign: bool = False) -> str:
    """
    `value` rounded to `decimals`, trailing zeros dropped (`1.500` -> `1.5`, `2.000` -> `2`).
    `sign` always writes the sign (`+0.5`). Rounding to zero never shows as `-0`.
    """
    if not math.isfinite(value):
        return "n/a"
    text = f"{value:.{max(decimals, 0)}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("-0", ""):
        text = "0"
    if sign and not text.startswith("-"):
        text = "+" + text
    return text


def format_significant(
    value: float, digits: int = 4, sign: bool = False, max_decimals: int = 3
) -> str:
    """
    `value` to `digits` significant digits but at most `max_decimals` decimals, trailing
    zeros dropped, never in exponent form: a readout stays short whatever the magnitude
    (`159.5`, `16.35`, `0.267`, `0.005`).
    """
    if not math.isfinite(value):
        return "n/a"
    if value == 0:
        return "+0" if sign else "0"
    decimals = min(max(digits - 1 - math.floor(math.log10(abs(value))), 0), max_decimals)
    return format_number(value, decimals, sign)


class ScopeDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    """
    A QDoubleSpinBox that reads and writes numbers the C way (dot, no group separator) and
    shows significant digits only. `decimals()` still bounds the precision a value keeps.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setLocale(QtCore.QLocale.c())

    def textFromValue(self, v: float) -> str:  # noqa: N802
        return format_number(v, self.decimals())

    def validate(  # noqa: N802
        self, text: str | None, pos: int
    ) -> tuple[QtGui.QValidator.State, str, int]:
        # A comma typed out of habit (a Polish or German keyboard) means the decimal point.
        return super().validate((text or "").replace(",", "."), pos)

    def valueFromText(self, text: str | None) -> float:  # noqa: N802
        return super().valueFromText((text or "").replace(",", "."))
