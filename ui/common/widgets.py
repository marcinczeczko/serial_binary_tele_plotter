"""
Custom UI Widgets module.

This module defines reusable custom widgets used primarily in the Control Panel.
It includes:
- CollapsibleGroup: An accordion-style container for grouping controls.
- YAxisControlWidget: A composite widget for configuring signal visualization (scale, visibility).
"""

from PyQt6 import QtCore, QtWidgets


class CollapsibleGroup(QtWidgets.QWidget):
    """
    A custom widget implementing an accordion-style collapsible container.

    It consists of a header button (QToolButton) and a content area. Clicking the
    header toggles the visibility of the content area. Only one group is typically
    intended to be expanded at a time (managed by the parent container).

    Signals:
        expanded (object): Emitted when this group is expanded by the user.
                           Payload: The CollapsibleGroup instance itself.
    """

    expanded = QtCore.pyqtSignal(object)

    def __init__(self, title: str):
        """
        Initializes the collapsible group.

        Args:
            title (str): The text to display on the toggle button.
        """
        super().__init__()

        # Toggle Button Configuration
        self.toggle = QtWidgets.QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self.toggle.setStyleSheet("QToolButton { border: none; font-weight: bold; color: #ccc; }")

        # Content Area Configuration
        self.content = QtWidgets.QWidget()
        self.content.setVisible(False)
        self.content_layout = QtWidgets.QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(10, 0, 0, 0)

        # Events
        self.toggle.toggled.connect(self._on_toggled)

        # Main Layout
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toggle)
        layout.addWidget(self.content)

    def _on_toggled(self, checked: bool):
        """
        Slot handling the toggle state change.

        Updates the arrow icon and visibility of the content area. Emits the
        'expanded' signal if the group was just opened.
        """
        self.content.setVisible(checked)
        self.toggle.setArrowType(
            QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow
        )
        if checked:
            self.expanded.emit(self)


class YAxisControlWidget(QtWidgets.QWidget):
    """
    Simple widget for a single signal: [X] Color_Icon Label_Name
    """

    def __init__(self, label: str, color: str):
        super().__init__()
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(5, 2, 5, 2)
        layout.setSpacing(10)

        # Checkbox widoczności
        self.enable_checkbox = QtWidgets.QCheckBox()
        self.enable_checkbox.setChecked(True)

        # Ikona koloru (mały kwadrat)
        self.color_icon = QtWidgets.QFrame()
        self.color_icon.setFixedSize(12, 12)
        self.color_icon.setStyleSheet(f"background-color: {color}; border-radius: 2px;")

        # Nazwa sygnału
        self.name_label = QtWidgets.QLabel(label)
        self.name_label.setStyleSheet("font-weight: bold; color: #ddd;")

        layout.addWidget(self.enable_checkbox)
        layout.addWidget(self.color_icon)
        layout.addWidget(self.name_label)
        layout.addStretch()  # Wszystko do lewej
