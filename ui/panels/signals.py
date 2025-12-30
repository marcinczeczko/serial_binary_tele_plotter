"""
Signal List Panel Module.

This module manages the dynamic list of signals displayed in an accordion-style
layout. It handles the creation of signal controls based on the stream configuration.
"""

from PyQt6 import QtCore, QtWidgets

from ui.common.widgets import CollapsibleGroup, YAxisControlWidget


class SignalListPanel(QtWidgets.QWidget):
    """
    Manages the dynamic list of signals and their visibility/scaling controls.
    """

    scale_changed = QtCore.pyqtSignal(str, float, float)
    signal_visibility_changed = QtCore.pyqtSignal(str, bool)

    def __init__(self):
        super().__init__()

        # Główny layout panelu
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        grp_sigs = QtWidgets.QGroupBox("Signals")
        l_sigs = QtWidgets.QVBoxLayout(grp_sigs)

        # Scroll Area dla długiej listy sygnałów
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        self.signals_container = QtWidgets.QWidget()
        self.signals_layout = QtWidgets.QVBoxLayout(self.signals_container)
        self.signals_layout.setSpacing(5)
        self.signals_layout.addStretch()

        scroll.setWidget(self.signals_container)
        l_sigs.addWidget(scroll)

        layout.addWidget(grp_sigs)

        # Stan wewnętrzny
        self.y_controls = []
        self._accordion_groups = []

    def rebuild_list(self, cfg: dict):
        """
        Dynamically rebuilds the list of signal controls based on configuration.
        """
        # 1. Wyczyść stary layout
        while self.signals_layout.count() > 1:
            item = self.signals_layout.takeAt(0)
            if item is not None:
                w = item.widget()
                if w is not None:
                    w.deleteLater()

        self.y_controls = []
        self._accordion_groups = []
        groups = {}

        # 2. Stwórz Grupy (Nagłówki akordeonu)
        for gid, gdata in sorted(cfg["groups"].items(), key=lambda x: x[1]["order"]):
            grp = CollapsibleGroup(gdata["label"])
            # Tutaj podłączamy brakujący slot
            grp.expanded.connect(self._on_group_expanded)

            self.signals_layout.insertWidget(self.signals_layout.count() - 1, grp)
            groups[gid] = grp
            self._accordion_groups.append(grp)

        # 3. Stwórz Kontrolki Sygnałów
        for sid, sdata in cfg["signals"].items():
            w = YAxisControlWidget(sdata["label"], sdata["color"])
            w.signal_id = sid
            w.min_edit.setValue(sdata["y_range"]["min"])
            w.max_edit.setValue(sdata["y_range"]["max"])

            # Podłączenie sygnałów z widgetu
            w.enable_checkbox.toggled.connect(
                lambda c, s=sid: self.signal_visibility_changed.emit(s, c)
            )
            # Opcjonalnie: automatyczne wysyłanie skali przy zmianie (bez przycisku Apply)
            w.min_edit.editingFinished.connect(lambda s=sid: self._emit_single_scale(s))
            w.max_edit.editingFinished.connect(lambda s=sid: self._emit_single_scale(s))

            target = groups.get(sdata["group"])
            if target:
                target.content_layout.addWidget(w)
            self.y_controls.append(w)

        # Rozwiń pierwszą grupę domyślnie
        if self._accordion_groups:
            self._accordion_groups[0].toggle.setChecked(True)

    def _on_group_expanded(self, sender):
        """
        Ensures only one accordion group is expanded at a time.
        This is the missing method.
        """
        for g in self._accordion_groups:
            if g is not sender:
                g.toggle.setChecked(False)

    def _emit_single_scale(self, signal_id):
        """Helper to find the widget and emit scale change."""
        for w in self.y_controls:
            if w.signal_id == signal_id:
                self.scale_changed.emit(signal_id, w.min_edit.value(), w.max_edit.value())
                break
