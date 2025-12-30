"""
Telemetry Plotting Module.

This module provides the `TelemetryPlot` widget, a specialized graph renderer
built on top of PyQtGraph.

Key Features:
1. **Multi-Signal Visualization**: Renders multiple signals on a shared X-axis (Time).
2. **Normalized ViewBoxes**: Uses a "Stacked ViewBox" architecture. All signals are
   normalized to 0.0-1.0 in the Engine. This widget overlays multiple invisible
   ViewBoxes to allow independent interaction/scaling if needed, though currently
   they share the geometry.
3. **Analysis Mode**: Freezes the plot (deep copy) to allow inspection of historical data.
4. **Interactive HUD**: Provides a crosshair cursor, interpolated value tooltips,
   and delta measurements (Anchor mode).
"""

import copy
from typing import Any, Dict, Optional

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore, QtWidgets

from core.types import PlotMode


class TelemetryPlot(QtWidgets.QWidget):
    """
    The main plotting widget for the application.

    It manages the PyQtGraph scene, handles mouse events for tooltips/anchors,
    and updates curves based on incoming data packets.

    Signals:
        cursor_moved (str): Emitted when the mouse hovers over the plot.
                            Payload: A status bar string with Time and Index.
    """

    cursor_moved = QtCore.pyqtSignal(str)

    def __init__(self):
        """Initializes the layout, graphics scene, and interactive items."""
        super().__init__()

        # State Management
        self.mode: PlotMode = PlotMode.LIVE

        # Stores references to curves and viewboxes: { 'signal_id': {'viewbox': vb, 'curve': c} }
        self.signal_views: Dict[str, Dict[str, Any]] = {}
        self.signal_colors: Dict[str, str] = {}

        # Data Snapshots
        self.last_packet: Optional[dict] = None
        self.analysis_packet: Optional[dict] = None

        # Measurement State
        self.anchor_time: Optional[float] = None
        self.anchor_values: Dict[str, float] = {}

        # --- UI Layout ---
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.graphics = pg.GraphicsLayoutWidget()
        layout.addWidget(self.graphics)

        # Create the main PlotItem.
        # Note: 'addPlot' is dynamically added by pyqtgraph, so we suppress type checkers.
        self.plot: pg.PlotItem = self.graphics.addPlot()  # type: ignore

        # Visual styling
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel("bottom", "Time [s]")
        # Hide the default left axis as we use normalized internal scales
        self.plot.getAxis("left").setVisible(False)

        # --- Interactive Overlay Items ---

        # 1. Vertical Cursor Line (Follows mouse)
        self.vLine = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#888", width=1))
        self.vLine.setAcceptHoverEvents(False)
        self.plot.addItem(self.vLine, ignoreBounds=True)

        # 2. Anchor Line (Fixed point for delta measurement)
        self.anchorLine = pg.InfiniteLine(
            angle=90, movable=False, pen=pg.mkPen("y", style=QtCore.Qt.PenStyle.DashLine, width=2)
        )
        self.anchorLine.setAcceptHoverEvents(False)
        self.anchorLine.setVisible(False)
        self.plot.addItem(self.anchorLine, ignoreBounds=True)

        # 3. HUD Label (Tooltip text in top-left corner)
        self.label = pg.TextItem(anchor=(0, 0))
        self.label.setAcceptHoverEvents(False)
        self.plot.addItem(self.label, ignoreBounds=True)

        # --- Event Wiring ---
        # We access the underlying GraphicsScene to listen for mouse events.
        # Note: We use '# type: ignore' because pyqtgraph adds these signals dynamically
        # at runtime, and static analysis tools (like Pylance) cannot see them on the
        # standard QGraphicsScene object.

        scene = self.plot.scene()
        scene.sigMouseMoved.connect(self.mouse_moved_handler)  # type: ignore
        scene.sigMouseClicked.connect(self.on_mouse_clicked)  # type: ignore

        # Connect to view range changes (zooming/panning)
        self.plot.sigRangeChanged.connect(self.update_hud_position)  # type: ignore

    @QtCore.pyqtSlot(dict)
    def on_data_ready(self, packet: dict):
        """
        Updates the plot with new data from the TelemetryEngine.

        Args:
            packet (dict): Data structure containing:
                - 'time': np.ndarray (X-axis)
                - 'signals': Dict[str, np.ndarray] (Normalized Y-data)
                - 'raw': Dict[str, np.ndarray] (Original Y-data for tooltips)
        """
        self.last_packet = packet

        # If in Analysis mode, we ignore new data updates to keep the view frozen
        if self.mode == PlotMode.ANALYSIS:
            return

        time = packet["time"]
        signals = packet["signals"]

        # Guard against empty initialization frames
        if len(time) == 0:
            return

        # Update valid signal curves
        for sig_id, y in signals.items():
            if sig_id in self.signal_views:
                self.signal_views[sig_id]["curve"].setData(time, y)

        # Auto-scroll X-axis to fit the time window
        self.plot.setXRange(time[0], time[-1], padding=0)  # type: ignore

        # Ensure HUD text stays in the corner relative to the new view range
        self.update_hud_position()

    @QtCore.pyqtSlot(bool)
    def set_paused(self, paused: bool):
        """
        Toggles between Live and Analysis mode.

        In Analysis mode, a deep copy of the current data is created. This allows
        the user to zoom/pan/inspect a snapshot while the background engine
        continues to overwrite the ring buffers.

        Args:
            paused (bool): True to freeze (Analysis), False to resume (Live).
        """
        if paused:
            self.mode = PlotMode.ANALYSIS
            # Create a snapshot to isolate UI from Engine thread memory
            self.analysis_packet = copy.deepcopy(self.last_packet)
        else:
            self.mode = PlotMode.LIVE
            self.analysis_packet = None

            # Reset measurement tools
            self.anchor_time = None
            self.anchorLine.setVisible(False)
            self.label.setHtml("")

    def mouse_moved_handler(self, pos: QtCore.QPointF):
        """
        Handles mouse movement to update the cursor position and tooltip.

        Args:
            pos (QPointF): Raw coordinates in the GraphicsScene.
        """
        # Map scene coordinates to the ViewBox coordinates (Time axis)
        vb = self.plot.vb
        if vb.sceneBoundingRect().contains(pos):  # type: ignore
            mousePoint = vb.mapSceneToView(pos)  # type: ignore
            self._process_mouse_movement(mousePoint.x())

    def _process_mouse_movement(self, x_raw: float):
        """
        Calculates the nearest valid timestamp and updates visual elements.
        """
        # Determine which dataset to use (Live vs Snapshot)
        ds = self.analysis_packet if self.mode == PlotMode.ANALYSIS else self.last_packet

        if not ds or len(ds["time"]) < 2:
            return

        # Clamp cursor to the actual data range (prevents reading outside buffer)
        t_start = ds["time"][0]
        t_end = ds["time"][-1]
        x_clamped = float(np.clip(x_raw, t_start, t_end))

        self.vLine.setPos(x_clamped)
        self.update_tooltip(x_clamped)

    def on_mouse_clicked(self, evt):
        """
        Handles mouse clicks to set the measurement anchor (Analysis mode only).

        Left-Click: Sets the 'zero point' (Anchor). Tooltips will show delta
        values relative to this point.
        """
        if self.mode != PlotMode.ANALYSIS:
            return

        if evt.button() != QtCore.Qt.MouseButton.LeftButton:
            return

        vb = self.plot.getViewBox()
        if vb.sceneBoundingRect().contains(evt.scenePos()):  # type: ignore
            mousePoint = vb.mapSceneToView(evt.scenePos())  # type: ignore

            # --- FIX: Używamy zmiennej lokalnej 't_stamp' ---
            # Dzięki temu Pylance wie, że to na pewno float (a nie None)
            t_stamp = mousePoint.x()

            # Aktualizacja stanu
            self.anchor_time = t_stamp

            # Aktualizacja UI
            self.anchorLine.setPos(t_stamp)
            self.anchorLine.setVisible(True)

            # Przekazujemy float, a nie Optional[float]
            self._capture_anchor_values(t_stamp)

            # Refresh tooltip immediately
            self._process_mouse_movement(t_stamp)

    def _capture_anchor_values(self, t_anchor: float):
        """
        Interpolates and stores signal values at the specific anchor time.
        """
        if not self.analysis_packet:
            return

        t_arr = self.analysis_packet["time"]
        raw = self.analysis_packet["raw"]

        # np.interp allows capturing values "between" samples
        self.anchor_values = {s: np.interp(t_anchor, t_arr, v) for s, v in raw.items()}

    def update_hud_position(self):
        """
        Keeps the HTML Label pinned to the top-left corner of the view,
        regardless of zooming or panning.
        """
        vb = self.plot.vb
        ranges = vb.viewRange()  # type: ignore

        xr: list[float] = ranges[0]
        yr: list[float] = ranges[1]

        # Position: 1% from left edge, 2% from top edge
        x_pos = xr[0] + 0.01 * (xr[1] - xr[0])
        y_pos = yr[1] - 0.02 * (yr[1] - yr[0])

        self.label.setPos(x_pos, y_pos)

    def update_tooltip(self, x_pos: float):
        """
        Constructs and renders the HTML tooltip.

        This method performs interpolation for all visible signals at the given
        timestamp and calculates deltas if an anchor is set.

        Args:
            x_pos (float): The timestamp at the cursor location.
        """
        ds = self.analysis_packet if self.mode == PlotMode.ANALYSIS else self.last_packet
        if not ds or len(ds["time"]) < 2:
            return

        t_arr = ds["time"]
        raw = ds["raw"]
        cur_t = float(x_pos)
        dt = t_arr[1] - t_arr[0]

        # Calculate array index (mostly for Status Bar info)
        idx = int(np.clip((cur_t - t_arr[0]) / dt, 0, len(t_arr) - 1))
        self.cursor_moved.emit(f"Cursor: {cur_t:.3f} s | Index: {idx}")

        # --- HTML Construction ---
        # 1. Header (Timestamp)
        html = (
            '<div style="background-color: rgba(0, 0, 0, 0.7); '
            'padding: 6px; font-family: Consolas, monospace; border: 1px solid #444;">'
        )
        html += f'<b style="color: white; font-size: 12px;">T: {cur_t:.3f} s</b>'

        # 2. Time Delta (if anchor active)
        if self.anchor_time:
            delta_t = cur_t - self.anchor_time
            html += f' <span style="color: #FFD700; font-size: 11px;">(Δ {delta_t:+.3f} s)</span>'

        html += "<br><hr style='margin: 4px 0;'>"

        # 3. Signal Rows
        for sid, vals in raw.items():
            # Only show visible signals
            if sid in self.signal_views and self.signal_views[sid]["curve"].isVisible():

                data_len = min(len(t_arr), len(vals))
                if data_len < 2:
                    continue

                # Interpolate value at cursor
                v = float(np.interp(cur_t, t_arr[:data_len], vals[:data_len]))

                color = self.signal_colors.get(sid, "#FFF")
                row = f'<span style="color: {color};">{sid}: <b>{v:>8.4f}</b>'

                # Value Delta (if anchor active)
                if self.anchor_time and sid in self.anchor_values:
                    delta_v = v - self.anchor_values[sid]
                    row += f' <span style="color: #aaa; font-size: 10px;">(Δ {delta_v:+.4f})</span>'

                html += row + "</span><br>"

        self.label.setHtml(html + "</div>")
        self.update_hud_position()

    @QtCore.pyqtSlot(dict)
    def configure_signals(self, signals_cfg: dict):
        """
        Reconfigures the plot area based on the stream definition.

        Architecture Note:
        To overlay signals with vastly different ranges (e.g. Error=0.01 vs PWM=100)
        without using multiple visible Y-axes, we use **Stacked ViewBoxes**.

        1. All signals are normalized (0.0-1.0) in the Engine.
        2. We create a separate ViewBox for each signal here.
        3. All ViewBoxes are linked to the main ViewBox's X-axis.
        4. We render the normalized data.

        This allows lines to be plotted relative to their own min/max range,
        appearing purely visual, while tooltips show the raw physics values.
        """
        # Cleanup old items
        for sid in list(self.signal_views.keys()):
            self.plot.scene().removeItem(self.signal_views[sid]["viewbox"])  # type: ignore
        self.plot.clear()
        self.signal_views.clear()
        self.signal_colors.clear()

        # Restore static tools
        self.plot.addItem(self.vLine, ignoreBounds=True)
        self.plot.addItem(self.anchorLine, ignoreBounds=True)
        self.plot.addItem(self.label, ignoreBounds=True)

        # The main ViewBox (drives the X-axis)
        base_vb = self.plot.getViewBox()

        for sid, sig in signals_cfg.items():
            self.signal_colors[sid] = sig["color"]

            # Create an invisible ViewBox for this signal
            vb = pg.ViewBox()
            vb.setMouseEnabled(x=False, y=False)
            vb.setYRange(0.0, 1.0)  # Normalized range
            self.plot.scene().addItem(vb)  # type: ignore

            # Link X-axis to the main plot so zooming works
            vb.setXLink(base_vb)

            # Create the curve
            c = pg.PlotDataItem(pen=pg.mkPen(color=sig["color"], width=2), skipFiniteCheck=True)
            vb.addItem(c)

            self.signal_views[sid] = {"viewbox": vb, "curve": c}

        # Hook up resize event to keep overlay ViewBoxes synchronized
        base_vb.sigResized.connect(self._update_views)  # type: ignore

    def _update_views(self):
        """
        Synchronizes the geometry of all overlay ViewBoxes with the main plot area.
        Called whenever the plot is resized.
        """
        rect = self.plot.getViewBox().sceneBoundingRect()  # type: ignore
        for s in self.signal_views.values():
            s["viewbox"].setGeometry(rect)

    @QtCore.pyqtSlot(str, bool)
    def set_signal_visible(self, sig_id: str, visible: bool):
        """Toggles the visibility of a specific signal curve."""
        if sig_id in self.signal_views:
            self.signal_views[sig_id]["curve"].setVisible(visible)
