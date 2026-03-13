"""
Signal List Panel Module (Simplified).

Handles the dynamic display of signal visibility controls in a flat list.
Removed: Accordion groups, collapsible logic.
"""

from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

# Zakładamy, że YAxisControlWidget jest teraz prostym widżetem (Label + Checkbox + Kolor)
from ui.common.widgets import YAxisControlWidget
from core.types import StreamConfig


class SignalListPanel(QtWidgets.QWidget):
    """
    Manages a flat list of signals and their visibility controls.
    """

    signal_visibility_changed = QtCore.pyqtSignal(str, bool)

    def __init__(self) -> None:
        super().__init__()

        # --- Main Layout ---
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        grp_sigs = QtWidgets.QGroupBox("Signals Visibility")
        l_sigs = QtWidgets.QVBoxLayout(grp_sigs)

        # --- Scroll Area ---
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        self.signals_container = QtWidgets.QWidget()
        self.signals_layout = QtWidgets.QVBoxLayout(self.signals_container)
        self.signals_layout.setSpacing(2)  # Mniejszy odstęp dla płaskiej listy
        self.signals_layout.addStretch()

        scroll.setWidget(self.signals_container)
        l_sigs.addWidget(scroll)
        layout.addWidget(grp_sigs)

    def rebuild_list(self, cfg: StreamConfig) -> None:
        """
        Rebuilds the flat list of signals.
        """
        # 1. Clear existing widgets
        while self.signals_layout.count() > 1:
            item = self.signals_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        # 2. Create Signal Controls (Flat List)
        signals = cfg.get("signals", {})
        for sid, sdata in signals.items():
            w = YAxisControlWidget(sdata["label"], sdata["color"], sdata["visible"])

            # Podpięcie checkboxa widoczności
            # captured_sid gwarantuje, że lambda użyje poprawnego ID w pętli
            w.enable_checkbox.toggled.connect(
                lambda checked, captured_sid=sid: self.signal_visibility_changed.emit(
                    captured_sid, checked
                )
            )

            # Wstawiamy do głównego layoutu przed spacerem
            self.signals_layout.insertWidget(self.signals_layout.count() - 1, w)
