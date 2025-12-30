"""
Plotting Area UI module.

This module contains the PlotArea widget which uses PyQtGraph to render
real-time telemetry data. It handles:
- Multi-signal visualization with shared X-axis.
- Independent Y-axis scaling (normalized view) via multiple ViewBoxes.
- Interactive cursors, tooltips, and measurements (delta calculation).
- Analysis mode (pausing and inspecting historical data).
"""

import copy

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore, QtWidgets

from core.types import PlotMode


class PlotArea(QtWidgets.QWidget):
    """
    Widget responsible for rendering the telemetry graphs.

    It supports multiple overlapping signal curves, each with its own
    normalized ViewBox to handle different scales (e.g., small error values
    vs large PWM outputs) on the same plot area.

    Signals:
        cursor_moved (str): Emitted when the mouse cursor moves over the plot.
                            Payload: Status string with time and index.
    """

    cursor_moved = QtCore.pyqtSignal(str)

    def __init__(self):
        """Initializes the plot widget, graphics layout, and interactive elements."""
        super().__init__()
        self.mode = PlotMode.LIVE
        self.signal_views = {}
        self.last_packet = None
        self.analysis_packet = None
        self.signal_colors = {}
        self.anchor_time = None
        self.anchor_values = {}

        # Layout setup
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.graphics = pg.GraphicsLayoutWidget()
        layout.addWidget(self.graphics)

        # --- FIX: Type ignore added for dynamic method ---
        self.plot = self.graphics.addPlot()  # type: ignore
        # -------------------------------------------------

        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel("bottom", "Time [s]")
        self.plot.getAxis("left").setVisible(False)

        # Interactive Elements (Cursor Line, Anchor Line, HUD Label)
        self.vLine = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#888", width=1))
        self.vLine.setAcceptHoverEvents(False)
        self.plot.addItem(self.vLine, ignoreBounds=True)
        self.anchorLine = pg.InfiniteLine(
            angle=90, movable=False, pen=pg.mkPen("y", style=QtCore.Qt.PenStyle.DashLine, width=2)
        )
        self.anchorLine.setAcceptHoverEvents(False)
        self.anchorLine.setVisible(False)
        self.plot.addItem(self.anchorLine, ignoreBounds=True)

        self.label = pg.TextItem(anchor=(0, 0))
        self.label.setAcceptHoverEvents(False)
        self.plot.addItem(self.label, ignoreBounds=True)

        # Event Connections
        self.plot.scene().sigMouseMoved.connect(self.mouse_moved_handler)
        self.plot.scene().sigMouseClicked.connect(self.on_mouse_clicked)
        self.plot.sigRangeChanged.connect(self.update_hud_position)

    @QtCore.pyqtSlot(dict)
    def on_data_ready(self, packet: dict):
        """
        Slot called when new data arrives from the worker.

        Updates the curves if in LIVE mode.

        Args:
            packet (dict): Dictionary containing 'time' (array) and 'signals' (dict of arrays).
        """
        self.last_packet = packet
        if self.mode == PlotMode.ANALYSIS:
            return
        time = packet["time"]
        signals = packet["signals"]
        if len(time) == 0:
            return
        for sig_id, y in signals.items():
            if sig_id in self.signal_views:
                self.signal_views[sig_id]["curve"].setData(time, y)
        self.plot.setXRange(time[0], time[-1], padding=0)
        self.update_hud_position()

    @QtCore.pyqtSlot(bool)
    def set_paused(self, paused: bool):
        """
        Toggles the plot mode between LIVE and ANALYSIS.

        When paused, a deep copy of the current data is made to allow inspection
        without data changing underneath.

        Args:
            paused (bool): True for ANALYSIS mode, False for LIVE mode.
        """
        if paused:
            self.mode = PlotMode.ANALYSIS
            self.analysis_packet = copy.deepcopy(self.last_packet)
        else:
            self.mode = PlotMode.LIVE
            self.analysis_packet = None
            self.anchor_time = None
            self.anchorLine.setVisible(False)
            self.label.setHtml("")

    def mouse_moved_handler(self, pos):
        """Handles mouse movement events to update the cursor line and tooltip."""
        vb = self.plot.vb
        if vb.sceneBoundingRect().contains(pos):
            mousePoint = vb.mapSceneToView(pos)
            self._process_mouse_movement(mousePoint.x())

    def _process_mouse_movement(self, x_raw):
        """Calculates cursor position and triggers tooltip update."""
        ds = self.analysis_packet if self.mode == PlotMode.ANALYSIS else self.last_packet
        if not ds or len(ds["time"]) < 2:
            return
        x_clamped = float(np.clip(x_raw, ds["time"][0], ds["time"][-1]))
        self.vLine.setPos(x_clamped)
        self.update_tooltip(x_clamped)

    def on_mouse_clicked(self, evt):
        """
        Handles mouse clicks to set the measurement anchor (Analysis mode only).

        Left-click places a 'zero point' (anchor) to measure time/value deltas.
        """
        if self.mode != PlotMode.ANALYSIS:
            return
        if evt.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        vb = self.plot.vb
        if vb.sceneBoundingRect().contains(evt.scenePos()):
            mousePoint = vb.mapSceneToView(evt.scenePos())
            self.anchor_time = mousePoint.x()
            self.anchorLine.setPos(self.anchor_time)
            self.anchorLine.setVisible(True)
            self._capture_anchor_values(self.anchor_time)
            self._process_mouse_movement(self.anchor_time)

    def _capture_anchor_values(self, t_anchor):
        """Records signal values at the anchor time for delta calculations."""
        if not self.analysis_packet:
            return
        t_arr = self.analysis_packet["time"]
        raw = self.analysis_packet["raw"]
        self.anchor_values = {s: np.interp(t_anchor, t_arr, v) for s, v in raw.items()}

    def update_hud_position(self):
        """Keeps the text label (HUD) pinned to the top-left corner of the view."""
        vb = self.plot.vb
        vb = self.plot.vb
        xr, yr = vb.viewRange()
        self.label.setPos(xr[0] + 0.01 * (xr[1] - xr[0]), yr[1] - 0.02 * (yr[1] - yr[0]))

    def update_tooltip(self, x_pos):
        """
        Generates and renders the HTML tooltip displaying signal values.

        Calculates interpolated values at x_pos and computes deltas if an anchor exists.
        """
        ds = self.analysis_packet if self.mode == PlotMode.ANALYSIS else self.last_packet
        if not ds or len(ds["time"]) < 2:
            return

        t_arr = ds["time"]
        raw = ds["raw"]
        cur_t = float(x_pos)
        dt = t_arr[1] - t_arr[0]

        # Calculate index (for status bar)
        idx = int(np.clip((cur_t - t_arr[0]) / dt, 0, len(t_arr) - 1))
        self.cursor_moved.emit(f"Cursor: {cur_t:.3f} s | Index: {idx}")

        # Build HTML
        html = f'<div style="background-color: rgba(0, 0, 0, 0.7); padding: 6px; font-family: Consolas, monospace; border: 1px solid #444;">'
        html += f'<b style="color: white; font-size: 12px;">T: {cur_t:.3f} s</b>'
        if self.anchor_time:
            html += f' <span style="color: #FFD700; font-size: 11px;">(Δ {cur_t - self.anchor_time:+.3f} s)</span>'
        html += "<br><hr style='margin: 4px 0;'>"

        for sid, vals in raw.items():
            if sid in self.signal_views and self.signal_views[sid]["curve"].isVisible():
                # Safety check for array lengths
                data_len = min(len(t_arr), len(vals))
                if data_len < 2:
                    continue

                # Interpolation
                v = float(np.interp(cur_t, t_arr[:data_len], vals[:data_len]))

                color = self.signal_colors.get(sid, "#FFF")
                row = f'<span style="color: {color};">{sid}: <b>{v:>8.4f}</b>'

                # Delta calculation
                if self.anchor_time and sid in self.anchor_values:
                    row += f' <span style="color: #aaa; font-size: 10px;">(Δ {v - self.anchor_values[sid]:+.4f})</span>'
                html += row + "</span><br>"

        self.label.setHtml(html + "</div>")
        self.update_hud_position()

    @QtCore.pyqtSlot(dict)
    def configure_signals(self, signals_cfg: dict):
        """
        Reconfigures the plot area based on the selected stream definition.

        Clears existing curves and ViewBoxes, then creates new ones for each
        signal defined in the configuration.
        """
        for sid in list(self.signal_views.keys()):
            self.plot.scene().removeItem(self.signal_views[sid]["viewbox"])
        self.plot.clear()
        self.signal_views.clear()
        self.signal_colors.clear()

        # Restore static items
        self.plot.addItem(self.vLine, ignoreBounds=True)
        self.plot.addItem(self.anchorLine, ignoreBounds=True)
        self.plot.addItem(self.label, ignoreBounds=True)

        base_vb = self.plot.getViewBox()

        for sid, sig in signals_cfg.items():
            self.signal_colors[sid] = sig["color"]

            # Create a dedicated ViewBox for each signal (allows independent Y scaling)
            vb = pg.ViewBox()
            vb.setMouseEnabled(x=False, y=False)
            vb.setYRange(0.0, 1.0)
            self.plot.scene().addItem(vb)
            vb.setXLink(base_vb)

            c = pg.PlotDataItem(pen=pg.mkPen(color=sig["color"], width=2), skipFiniteCheck=True)
            vb.addItem(c)
            self.signal_views[sid] = {"viewbox": vb, "curve": c}
        base_vb.sigResized.connect(self._update_views)

    def _update_views(self):
        """Synchronizes the geometry of all signal ViewBoxes with the main plot area."""
        rect = self.plot.getViewBox().sceneBoundingRect()
        for s in self.signal_views.values():
            s["viewbox"].setGeometry(rect)

    @QtCore.pyqtSlot(str, bool)
    def set_signal_visible(self, sig_id, visible):
        """Toggles the visibility of a specific signal curve."""
        if sig_id in self.signal_views:
            self.signal_views[sig_id]["curve"].setVisible(visible)
