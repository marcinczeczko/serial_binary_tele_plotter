"""
Signal List Panel Module.

This module provides the `SignalListPanel` widget, which manages the dynamic display
of signal controls (visibility).

Features:
- **Dynamic Content**: The list is rebuilt at runtime based on the JSON stream configuration.
- **Accordion Layout**: Signals are grouped logically, allowing only one group
  to be expanded at a time to save screen space.
- **Scrollable Area**: Handles cases with many signals without breaking the UI layout.
"""

from typing import Dict, List

from PyQt6 import QtCore, QtWidgets

from ui.common.widgets import CollapsibleGroup, YAxisControlWidget


class SignalListPanel(QtWidgets.QWidget):
    """
    Manages the dynamic list of signals and their visibility controls.

    This widget acts as a container for `YAxisControlWidget` items, organized
    into `CollapsibleGroup` categories.

    Attributes:
        signal_visibility_changed (pyqtSignal): Emitted when a signal checkbox is toggled.
            Payload: (signal_id: str, is_visible: bool).
    """

    signal_visibility_changed = QtCore.pyqtSignal(str, bool)

    def __init__(self):
        """Initializes the layout, scroll area, and internal storage lists."""
        super().__init__()

        # --- Main Layout ---
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # GroupBox container
        grp_sigs = QtWidgets.QGroupBox("Signals")
        l_sigs = QtWidgets.QVBoxLayout(grp_sigs)

        # --- Scroll Area Setup ---
        # Essential for lists that can grow beyond the window height
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        # The internal widget that holds the actual layout items
        self.signals_container = QtWidgets.QWidget()
        self.signals_layout = QtWidgets.QVBoxLayout(self.signals_container)
        self.signals_layout.setSpacing(5)

        # Add a stretch item at the end to push all widgets to the top
        self.signals_layout.addStretch()

        scroll.setWidget(self.signals_container)
        l_sigs.addWidget(scroll)

        layout.addWidget(grp_sigs)

        # --- Internal State ---
        # Keep references to prevent garbage collection and allow access by ID
        self.y_controls: List[YAxisControlWidget] = []
        self._accordion_groups: List[CollapsibleGroup] = []

    def rebuild_list(self, cfg: dict) -> None:
        """
        Dynamically rebuilds the list of signal controls based on configuration.

        Args:
            cfg (dict): The stream configuration dictionary containing 'groups' and 'signals'.
        """
        # 1. Clear existing layout
        # We iterate while count > 1 to preserve the final 'addStretch' item.
        # This is more efficient than recreating the stretch every time.
        while self.signals_layout.count() > 1:
            item = self.signals_layout.takeAt(0)
            if item is not None:
                w = item.widget()
                if w is not None:
                    w.deleteLater()

        # Reset internal lists
        self.y_controls.clear()
        self._accordion_groups.clear()

        # Map GroupID -> Widget instance for easy lookup when adding signals
        groups_map: Dict[str, CollapsibleGroup] = {}

        # 2. Create Groups (Accordion Headers)
        # Sort groups by the 'order' field defined in JSON
        sorted_groups = sorted(cfg["groups"].items(), key=lambda x: x[1]["order"])

        for gid, gdata in sorted_groups:
            grp = CollapsibleGroup(gdata["label"])

            # Connect the expansion signal to implement accordion logic
            grp.expanded.connect(self._on_group_expanded)

            # Insert before the spacer (stretch) at the bottom
            self.signals_layout.insertWidget(self.signals_layout.count() - 1, grp)

            groups_map[gid] = grp
            self._accordion_groups.append(grp)

        # 3. Create Signal Controls
        for sid, sdata in cfg["signals"].items():
            # Instantiate the control widget
            w = YAxisControlWidget(sdata["label"], sdata["color"])
            w.signal_id = sid  # Inject ID for later reference

            # Connect Signals
            # Note: We use default argument `s=sid` to capture the current loop variable value
            w.enable_checkbox.toggled.connect(
                lambda c, s=sid: self.signal_visibility_changed.emit(s, c)
            )
            # Add widget to the appropriate group layout
            target_group = groups_map.get(sdata["group"])
            if target_group:
                target_group.content_layout.addWidget(w)

            self.y_controls.append(w)

        # 4. Default State: Expand the first group
        if self._accordion_groups:
            self._accordion_groups[0].toggle.setChecked(True)

    def _on_group_expanded(self, sender: CollapsibleGroup) -> None:
        """
        Slot called when a group is expanded.
        Ensures only one accordion group is expanded at a time (Exclusive Expansion).

        Args:
            sender (CollapsibleGroup): The group that was just expanded.
        """
        for g in self._accordion_groups:
            if g is not sender:
                # Collapse all other groups
                g.toggle.setChecked(False)
