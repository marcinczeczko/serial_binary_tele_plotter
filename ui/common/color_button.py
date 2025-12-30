"""
Reusable UI Component: Color Picker Button.
"""

from PyQt6 import QtCore, QtGui, QtWidgets


class ColorButton(QtWidgets.QPushButton):
    """
    Custom widget: A button that opens a color picker and displays the selected color code.
    Updates its background color and text contrast automatically.
    """

    colorChanged = QtCore.pyqtSignal(str)

    def __init__(self, hex_color: str = "#FFFFFF", parent=None):
        super().__init__(parent)
        self.hex_color = hex_color
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(24)

        self.clicked.connect(self.pick_color)
        self.refresh_style()

    def refresh_style(self):
        """Updates the button background and text color based on brightness."""
        text_col = "black"
        try:
            c = QtGui.QColor(self.hex_color)
            if c.lightness() < 128:
                text_col = "white"
        except Exception:
            pass

        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {self.hex_color}; 
                color: {text_col}; 
                border: 1px solid #555; 
                border-radius: 2px;
                font-family: monospace;
                font-weight: bold;
                padding: 0px;
            }}
            QPushButton:hover {{
                border: 1px solid #FFF;
            }}
        """
        )
        self.setText(self.hex_color)

    def set_color(self, hex_color: str):
        self.hex_color = hex_color
        self.refresh_style()

    def pick_color(self):
        color = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(self.hex_color), self, "Pick Signal Color"
        )
        if color.isValid():
            self.set_color(color.name())
            self.colorChanged.emit(color.name())
