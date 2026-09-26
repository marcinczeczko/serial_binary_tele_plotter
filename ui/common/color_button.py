"""
Reusable UI Component: Color Picker Button.
"""

from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets

from styles import NUMBER_CSS, TEXT, TEXT_BRIGHT

CHIP_PX = 20


class ColorButton(QtWidgets.QPushButton):
    """
    A colour chip and its hex (R11.4): a flat square in the colour, the code beside it.
    Clicking opens the colour picker; `colorChanged` reports a new pick.
    """

    colorChanged = QtCore.pyqtSignal(str)

    def __init__(self, hex_color: str = "#FFFFFF", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.hex_color = hex_color
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(24)
        self.setIconSize(QtCore.QSize(CHIP_PX, CHIP_PX))
        self.setStyleSheet(
            "QPushButton { background: transparent; border: none; text-align: left;"
            f" padding: 0; color: {TEXT}; {NUMBER_CSS} }}"
            f" QPushButton:hover {{ color: {TEXT_BRIGHT}; background: transparent; }}"
        )
        self.clicked.connect(self.pick_color)
        self.refresh_style()

    def refresh_style(self) -> None:
        """The chip in the colour, and the code as text."""
        pixmap = QtGui.QPixmap(CHIP_PX, CHIP_PX)
        pixmap.fill(QtGui.QColor(self.hex_color))
        self.setIcon(QtGui.QIcon(pixmap))
        self.setText(f"  {self.hex_color.upper()}")

    def set_color(self, hex_color: str) -> None:
        self.hex_color = hex_color
        self.refresh_style()

    def pick_color(self) -> None:
        color = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(self.hex_color), self, "Pick Signal Color"
        )
        if color.isValid():
            self.set_color(color.name())
            self.colorChanged.emit(color.name())
