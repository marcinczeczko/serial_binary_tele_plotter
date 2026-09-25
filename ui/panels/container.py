"""
The dashboard's controls and their logic (R6.1): the main window places the pieces.

- `conn_panel`: the toolbar's port, baud, Connect and Pause.
- `stream_tabs` and `time_panel`: above the plot (the Period / History buttons open the
  time panel).
- `sig_panel`: the Signals dock (left), grouped by lane, with the cursor readout.
- `controls_stack`: the Controls dock (right). It shows the control panel the shown
  stream names in `controls`, one generated per streams.json `panels` entry (R5.2); for
  a text profile, the `terminal` instead (R8.5).
- `trigger_panel`: its `setup` opens from the toolbar's Trigger button, and its `results`
  (step response) is a tab of the right dock.

With a `UiState`, the shown stream, visibility, lane moves, panel values, presets and each
panel's Live mode are remembered between runs (R5.3, R6.4) and applied on top of
streams.json.
"""

from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from core.config import StreamConfigLoader
from core.types import StreamConfig
from ui.panels.command_panel import CommandPanel
from ui.panels.connection import ConnectionPanel
from ui.panels.signals import SignalListPanel
from ui.panels.stream_tabs import StreamTabs
from ui.panels.terminal import Terminal
from ui.panels.timing import TimeConfigPanel
from ui.panels.trigger import TriggerPanel
from ui.ui_state import UiState, apply_view_overrides

NO_CONTROLS = (
    "This stream has no control panel.\n\n"
    "Add a `panels` entry and name it in the stream's `controls` (streams.json)."
)


class MainControlPanel(QtWidgets.QWidget):
    """
    Owns the dashboard's controls (hidden itself: its pieces live in the toolbar, above the
    plot and in the docks) and the logic between them: stream selection, control panels,
    persistence.
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
    controls_changed = QtCore.pyqtSignal(str)  # the shown control panel's title ("" for none)

    def __init__(self, stream_loader: StreamConfigLoader, ui_state: UiState | None = None) -> None:
        super().__init__()
        self.setVisible(False)
        self.ui_state = ui_state
        self.stream_loader = stream_loader

        self.conn_panel = ConnectionPanel()
        self.stream_tabs = StreamTabs()
        self.time_panel = TimeConfigPanel()
        self.sig_panel = SignalListPanel()
        self.trigger_panel = TriggerPanel(self)

        self.controls_stack = QtWidgets.QStackedWidget()
        self.empty_controls = QtWidgets.QLabel(NO_CONTROLS)
        self.empty_controls.setWordWrap(True)
        self.empty_controls.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.empty_controls.setContentsMargins(12, 12, 12, 12)
        self.empty_controls.setStyleSheet("color: #9a9a9a;")
        self.controls_stack.addWidget(self.empty_controls)
        self.terminal = Terminal()  # a text profile's controls (R8.5)
        self.controls_stack.addWidget(self.terminal)
        self.terminal.ending_changed.connect(self._remember_ending)
        if ui_state is not None:
            self.terminal.set_ending(ui_state.line_ending())
        # Owned here until the main window places them (never left to garbage collection).
        for widget in (
            self.conn_panel,
            self.stream_tabs,
            self.time_panel,
            self.sig_panel,
            self.controls_stack,
            self.trigger_panel.setup,
            self.trigger_panel.results,
        ):
            widget.setParent(self)

        # Generated from streams.json `panels`, by key (R5.2).
        self.control_panels: dict[str, CommandPanel] = {}
        self._build_control_panels()

        for sid, s in self.stream_loader.list_streams().items():
            self.stream_tabs.addItem(s["name"], sid)
        remembered = ui_state.stream() if ui_state else None
        if remembered is not None and self.stream_tabs.findData(remembered) >= 0:
            self.stream_tabs.setCurrentIndex(self.stream_tabs.findData(remembered))
        self.stream_tabs.currentChanged.connect(self._on_stream_selection)

        self._connect_signals()
        if self.stream_tabs.count() > 0:
            self._on_stream_selection(self.stream_tabs.currentIndex())

    def _connect_signals(self) -> None:
        self.conn_panel.connection_requested.connect(self.connection_requested)
        self.conn_panel.pause_requested.connect(self.pause_requested)
        self.time_panel.period_changed.connect(self.period_changed)
        self.time_panel.samples_changed.connect(self.samples_changed)
        self.sig_panel.signal_visibility_changed.connect(self.signal_visibility_changed)
        self.sig_panel.signal_lane_changed.connect(self.signal_lane_changed)
        # Remembered between runs (R5.3)
        self.sig_panel.signal_visibility_changed.connect(self._remember_visibility)
        self.sig_panel.signal_lane_changed.connect(self._remember_lane)

    # --- control panels ---

    def _build_control_panels(self) -> None:
        """(Re)creates one panel per valid streams.json panel, with remembered state."""
        for panel in self.control_panels.values():
            self.controls_stack.removeWidget(panel)
            panel.deleteLater()  # on this thread, never left to garbage collection
        self.control_panels.clear()
        for key, definition in self.stream_loader.panels.items():
            panel = CommandPanel(definition)
            if self.ui_state is not None:
                panel.set_values(self.ui_state.panel_values(key))
                panel.reset_links()
                panel.set_presets(self.ui_state.presets(key))
                panel.set_live(self.ui_state.panel_live(key))
            panel.values_changed.connect(lambda k=key: self._remember_panel_values(k))
            panel.preset_saved.connect(
                lambda name, values, k=key: self._save_preset(k, name, values)
            )
            panel.preset_deleted.connect(lambda name, k=key: self._delete_preset(k, name))
            panel.live_changed.connect(lambda live, k=key: self._remember_live(k, live))
            panel.send_requested.connect(self.send_requested)
            self.controls_stack.addWidget(panel)
            self.control_panels[key] = panel

    def current_control_panel(self) -> CommandPanel | None:
        panel = self.stream_loader.panel_for(self.current_stream_key())
        return self.control_panels.get(panel.key) if panel is not None else None

    def _remember_panel_values(self, key: str) -> None:
        if self.ui_state is not None and key in self.control_panels:
            self.ui_state.set_panel_values(key, self.control_panels[key].values())

    def _save_preset(self, key: str, name: str, values: dict[str, dict[str, float]]) -> None:
        if self.ui_state is not None:
            self.ui_state.save_preset(key, name, values)

    def _delete_preset(self, key: str, name: str) -> None:
        if self.ui_state is not None:
            self.ui_state.delete_preset(key, name)

    def _remember_ending(self, name: str) -> None:
        if self.ui_state is not None:
            self.ui_state.set_line_ending(name)

    @property
    def is_text(self) -> bool:
        return self.stream_loader.profile.format == "text"

    def show_controls(self) -> None:
        """The terminal (text profile), else the shown stream's panel, else the hint."""
        if self.is_text:
            self.controls_stack.setCurrentWidget(self.terminal)
            self.controls_changed.emit("Terminal")
            return
        panel = self.current_control_panel()
        self.controls_stack.setCurrentWidget(panel if panel is not None else self.empty_controls)
        self.controls_changed.emit(panel.panel.title if panel is not None else "")

    def _remember_live(self, key: str, live: bool) -> None:
        if self.ui_state is not None:
            self.ui_state.set_panel_live(key, live)

    # --- view overrides ---

    def _remember_visibility(self, sid: str, visible: bool) -> None:
        key = self.current_stream_key()
        if self.ui_state is not None and key is not None:
            self.ui_state.set_visible(key, sid, visible)

    def _remember_lane(self, sid: str, lane: str, label: str) -> None:
        key = self.current_stream_key()
        if self.ui_state is not None and key is not None:
            self.ui_state.set_lane(key, sid, lane, label)

    # --- stream selection ---

    def _on_stream_selection(self, idx: int) -> None:
        """Shows another stream: its signals, its control panel, its trigger choices."""
        sid = self.stream_tabs.itemData(idx)
        if sid is None:
            return

        cfg = self._effective_config(sid)
        if self.ui_state is not None:
            self.ui_state.set_stream(sid)

        self.show_controls()

        self.sig_panel.rebuild_list(cfg)
        self.trigger_panel.set_signals(cfg)
        self.stream_changed.emit(cfg)

    def select_stream(self, key: str) -> None:
        index = self.stream_tabs.findData(key)
        if index >= 0:
            self.stream_tabs.setCurrentIndex(index)

    def get_initial_sample_count(self) -> int:
        return self.time_panel.get_samples()

    def current_stream_key(self) -> str | None:
        key = self.stream_tabs.currentData()
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
            self._on_stream_selection(self.stream_tabs.currentIndex())

    def reload_profile(self) -> None:
        """
        Shows another profile (R8.2; the loader has opened it): its control panels and
        streams, on the stream this profile showed last. A profile without streams shows
        an empty plot.
        """
        self._build_control_panels()
        if self.ui_state is not None:
            self.terminal.set_ending(self.ui_state.line_ending())
        self.stream_tabs.blockSignals(True)
        self.stream_tabs.clear()
        for sid, s in self.stream_loader.list_streams().items():
            self.stream_tabs.addItem(s["name"], sid)
        remembered = self.ui_state.stream() if self.ui_state is not None else None
        idx = max(self.stream_tabs.findData(remembered), 0)
        if self.stream_tabs.count() > 0:
            self.stream_tabs.setCurrentIndex(idx)
        self.stream_tabs.blockSignals(False)
        if self.stream_tabs.count() > 0:
            self._on_stream_selection(idx)
            return
        empty: StreamConfig = {"name": "", "frame": {"fields": []}, "signals": {}}
        self.show_controls()
        self.sig_panel.rebuild_list(empty)
        self.trigger_panel.set_signals(empty)
        self.stream_changed.emit(empty)

    def reload_streams(self) -> None:
        """
        Reloads configuration from disk and refreshes the streams, keeping the current
        stream selected if it still exists. The selection is always re-applied, so the plot
        and engine pick up edits to the current stream.
        """
        current = self.stream_tabs.currentData()
        self.stream_loader.load()
        self._build_control_panels()

        self.stream_tabs.blockSignals(True)
        self.stream_tabs.clear()
        for sid, s in self.stream_loader.list_streams().items():
            self.stream_tabs.addItem(s["name"], sid)
        idx = max(self.stream_tabs.findData(current), 0)
        if self.stream_tabs.count() > 0:
            self.stream_tabs.setCurrentIndex(idx)
        self.stream_tabs.blockSignals(False)

        if self.stream_tabs.count() > 0:
            self._on_stream_selection(idx)
