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
    A widget for configuring the visualization parameters of a single signal.

    It provides controls for:
    - Toggling signal visibility (Checkbox).
    - Setting manual Y-axis limits (Min/Max SpinBoxes).
    - Visualizing the signal color.
    """

    def __init__(self, name: str, color: str):
        """
        Initializes the control widget for a specific signal.

        Args:
            name (str): The display name of the signal.
            color (str): Hex color code string (e.g., "#FF0000") for the indicator.
        """
        super().__init__()
        self.signal_id = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 8)

        # Header Row: Color Indicator + Name Checkbox
        h_layout = QtWidgets.QHBoxLayout()
        color_lbl = QtWidgets.QLabel("●")
        color_lbl.setStyleSheet(f"color: {color}; font-size: 16px;")

        self.enable_checkbox = QtWidgets.QCheckBox(name)
        self.enable_checkbox.setChecked(True)

        h_layout.addWidget(color_lbl)
        h_layout.addWidget(self.enable_checkbox)
        h_layout.addStretch()

        # Range Row: Min/Max inputs
        r_layout = QtWidgets.QHBoxLayout()
        self.min_edit = QtWidgets.QDoubleSpinBox()
        self.max_edit = QtWidgets.QDoubleSpinBox()

        for w in (self.min_edit, self.max_edit):
            w.setRange(-1e6, 1e6)
            w.setDecimals(2)
            w.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)

        r_layout.addWidget(QtWidgets.QLabel("Min:"))
        r_layout.addWidget(self.min_edit)
        r_layout.addWidget(QtWidgets.QLabel("Max:"))
        r_layout.addWidget(self.max_edit)

        # Optional Lock (Reserved for future auto-scale locking logic)
        self.lock_checkbox = QtWidgets.QCheckBox("Lock Y")

        # Final Assembly
        layout.addLayout(h_layout)
        layout.addLayout(r_layout)
        layout.addWidget(self.lock_checkbox)
