"""
Main Control Panel Container Module.

This module aggregates specialized sub-panels into a single sidebar widget. A
**QStackedWidget** shows the control panel the shown stream names in `controls`, one of
the panels generated from streams.json `panels` (R5.2).

With a `UiState`, the shown stream, visibility, lane moves and panel values are remembered
between runs (R5.3) and applied on top of streams.json.
"""

from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from core.config import StreamConfigLoader
from core.types import StreamConfig
from ui.common.widgets import CollapsableSection
from ui.panels.command_panel import CommandPanel
from ui.panels.connection import ConnectionPanel
from ui.panels.signals import SignalListPanel
from ui.panels.timing import TimeConfigPanel
from ui.panels.trigger import TriggerPanel
from ui.ui_state import UiState, apply_view_overrides


class MainControlPanel(QtWidgets.QWidget):
    """
    The main sidebar widget containing all configuration controls.
    """

    # --- Public Signals ---
    connection_requested = QtCore.pyqtSignal(str, int)
    pause_requested = QtCore.pyqtSignal(bool)
    send_requested = QtCore.pyqtSignal(object)  # command_panel.SendRequest

    period_changed = QtCore.pyqtSignal(float)  # ms, for the shown stream
    samples_changed = QtCore.pyqtSignal(int)
    stream_changed = QtCore.pyqtSignal(dict)
    signal_visibility_changed = QtCore.pyqtSignal(str, bool)
    signal_lane_changed = QtCore.pyqtSignal(str, str, str)  # signal id, lane key, lane label

    def __init__(self, stream_loader: StreamConfigLoader, ui_state: UiState | None = None) -> None:
        super().__init__()
        self.ui_state = ui_state

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(10)

        # 1. Fixed Top Panel (Connection)
        self.conn_panel = ConnectionPanel()

        # 2. Stream Selection (Shared)
        self.grp_stream = QtWidgets.QGroupBox("Stream Type")
        l_stream = QtWidgets.QVBoxLayout(self.grp_stream)
        self.stream_loader = stream_loader
        self.payload_combo = QtWidgets.QComboBox()

        for sid, s in self.stream_loader.list_streams().items():
            self.payload_combo.addItem(s["name"], sid)
        remembered = ui_state.stream() if ui_state else None
        if remembered is not None and self.payload_combo.findData(remembered) >= 0:
            self.payload_combo.setCurrentIndex(self.payload_combo.findData(remembered))

        self.payload_combo.currentIndexChanged.connect(self._on_stream_selection)
        l_stream.addWidget(self.payload_combo)

        # 3. Dynamic Stacked Panel (Context-Aware UI)
        self.dynamic_stack = QtWidgets.QStackedWidget()

        self.empty_panel = QtWidgets.QWidget()
        self.dynamic_stack.addWidget(self.empty_panel)
        # Generated from streams.json `panels`, by key (R5.2).
        self.control_panels: dict[str, CommandPanel] = {}
        self.control_sections: dict[str, CollapsableSection] = {}
        self._build_control_panels()

        # 4. Fixed Bottom Panels
        self.time_panel = TimeConfigPanel()
        self.trigger_panel = TriggerPanel()
        self.trigger_section = CollapsableSection("Trigger / Step Response", self.trigger_panel)
        self.sig_panel = SignalListPanel()

        # 5. Assemble Main Layout
        layout.addWidget(self.conn_panel)
        layout.addWidget(self.grp_stream)

        # Insert the dynamic stack here
        layout.addWidget(self.dynamic_stack)

        layout.addWidget(self.time_panel)
        layout.addWidget(self.trigger_section)
        layout.addWidget(self.sig_panel, 1)

        # 6. Wiring & Init
        self._connect_signals()

        if self.payload_combo.count() > 0:
            self._on_stream_selection(self.payload_combo.currentIndex())

    def _connect_signals(self) -> None:
        """Wires internal signals. All panels are wired even if hidden."""
        # Global
        self.conn_panel.connection_requested.connect(self.connection_requested)
        self.conn_panel.pause_requested.connect(self.pause_requested)
        self.time_panel.period_changed.connect(self.period_changed)
        self.time_panel.samples_changed.connect(self.samples_changed)
        self.sig_panel.signal_visibility_changed.connect(self.signal_visibility_changed)
        self.sig_panel.signal_lane_changed.connect(self.signal_lane_changed)

        # Remembered between runs (R5.3)
        self.sig_panel.signal_visibility_changed.connect(self._remember_visibility)
        self.sig_panel.signal_lane_changed.connect(self._remember_lane)

    def _build_control_panels(self) -> None:
        """(Re)creates one panel per valid streams.json panel, with remembered values."""
        for key, section in self.control_sections.items():
            self.dynamic_stack.removeWidget(section)
            # A collapsed section's content has no parent: delete both explicitly (on
            # this thread), never leave them to garbage collection.
            self.control_panels[key].deleteLater()
            section.deleteLater()
        self.control_panels.clear()
        self.control_sections.clear()
        for key, definition in self.stream_loader.panels.items():
            panel = CommandPanel(definition)
            if self.ui_state is not None:
                panel.set_values(self.ui_state.panel_values(key))
            panel.values_changed.connect(lambda k=key: self._remember_panel_values(k))
            panel.send_requested.connect(self.send_requested)
            section = CollapsableSection(definition.title, panel)
            self.dynamic_stack.addWidget(section)
            self.control_panels[key] = panel
            self.control_sections[key] = section

    def _remember_panel_values(self, key: str) -> None:
        if self.ui_state is not None and key in self.control_panels:
            self.ui_state.set_panel_values(key, self.control_panels[key].values())

    def _remember_visibility(self, sid: str, visible: bool) -> None:
        key = self.current_stream_key()
        if self.ui_state is not None and key is not None:
            self.ui_state.set_visible(key, sid, visible)

    def _remember_lane(self, sid: str, lane: str, label: str) -> None:
        key = self.current_stream_key()
        if self.ui_state is not None and key is not None:
            self.ui_state.set_lane(key, sid, lane, label)

    def _on_stream_selection(self, idx: int) -> None:
        """
        Handles switching the data config AND the visible control UI.
        """
        sid = self.payload_combo.itemData(idx)
        if sid is None:
            return

        cfg = self._effective_config(sid)
        if self.ui_state is not None:
            self.ui_state.set_stream(sid)

        # The stream's control panel, if it names one; nothing otherwise.
        panel = self.stream_loader.panel_for(sid)
        if panel is None:
            self.dynamic_stack.setVisible(False)
        else:
            self.dynamic_stack.setCurrentWidget(self.control_sections[panel.key])
            self.dynamic_stack.setVisible(True)

        # Update Signal List & Notify Main Window
        self.sig_panel.rebuild_list(cfg)
        self.trigger_panel.set_signals(cfg)
        self.stream_changed.emit(cfg)

    def get_initial_sample_count(self) -> int:
        return self.time_panel.get_samples()

    def current_stream_key(self) -> str | None:
        key = self.payload_combo.currentData()
        return key if isinstance(key, str) else None

    def get_current_stream_config(self) -> StreamConfig | None:
        """The shown stream as displayed: streams.json plus remembered view overrides."""
        sid = self.current_stream_key()
        return self._effective_config(sid) if sid is not None else None

    def _effective_config(self, sid: str) -> StreamConfig:
        cfg = self.stream_loader.get_stream(sid)
        if self.ui_state is None:
            return cfg
        return apply_view_overrides(cfg, self.ui_state.visibility(sid), self.ui_state.lanes(sid))

    def reset_view(self) -> None:
        """Forgets the shown stream's visibility and lane moves: back to streams.json."""
        sid = self.current_stream_key()
        if self.ui_state is not None and sid is not None:
            self.ui_state.reset_view(sid)
            self._on_stream_selection(self.payload_combo.currentIndex())

    def reload_streams(self) -> None:
        """
        Reloads configuration from disk and refreshes the stream list, keeping the current
        stream selected if it still exists. The selection is always re-applied, so the plot
        and engine pick up edits to the current stream.
        """
        current = self.payload_combo.currentData()
        self.stream_loader.load()
        self._build_control_panels()

        self.payload_combo.blockSignals(True)
        self.payload_combo.clear()
        for sid, s in self.stream_loader.list_streams().items():
            self.payload_combo.addItem(s["name"], sid)
        idx = max(self.payload_combo.findData(current), 0)
        if self.payload_combo.count() > 0:
            self.payload_combo.setCurrentIndex(idx)
        self.payload_combo.blockSignals(False)

        if self.payload_combo.count() > 0:
            self._on_stream_selection(idx)
