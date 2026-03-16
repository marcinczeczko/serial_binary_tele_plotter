"""
Telemetry Plotting Module (Absolute Raw Mode).
No normalization. No scaling math. No ViewBox stacking.
What comes in packet['signals'] is displayed exactly on the Y-axis.
"""

from __future__ import annotations

import time
from typing import Any, TypedDict

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore, QtWidgets

from core.types import PlotMode, PlotPacketWithRaw, SignalsConfig, StreamSignalConfig


class _SignalView(TypedDict):
    curve: pg.PlotDataItem
    config: StreamSignalConfig


class TelemetryPlot(QtWidgets.QWidget):
    cursor_moved = QtCore.pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()

        # --- State ---
        self.mode: PlotMode = PlotMode.LIVE

        self._render_busy: bool = False
        self._last_render_ts: float = 0.0
        self._min_render_interval: float = 0.07

        # Performance: Throttle range updates
        self._last_range_update_ts: float = 0.0
        self._range_update_interval: float = 0.2  # Update ranges every 200ms instead of every frame

        # signal_views: we hold references to curves here
        # Format: { "signal_id": { "curve": pg.PlotDataItem, "config": dict } }
        self.signal_views: dict[str, _SignalView] = {}

        self.last_packet: PlotPacketWithRaw | None = None
        self.analysis_packet: PlotPacketWithRaw | None = None

        self.anchor_time: float | None = None
        self.anchor_values: dict[str, float] = {}

        # --- UI Layout ---
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.graphics = pg.GraphicsLayoutWidget()
        layout.addWidget(self.graphics)

        # Główny PlotItem
        self.plot: pg.PlotItem = self.graphics.addPlot()
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel("bottom", "Time [s]")

        # --- PERFORMANCE CRITICAL ---
        self.plot.setDownsampling(mode="peak")
        self.plot.setClipToView(True)

        # TURN ON left axis and enable auto-scaling
        self.plot.getAxis("left").setVisible(True)
        self.plot.enableAutoRange(axis="y")
        # Optionsl: block minimal range to prevent wild swings when values are zero
        # self.plot.setLimits(yMin=-1000, yMax=1000)

        # --- Interactive Tools ---
        self.vLine = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#888", width=1))
        self.plot.addItem(self.vLine, ignoreBounds=True)

        self.anchorLine = pg.InfiniteLine(
            angle=90,
            movable=False,
            pen=pg.mkPen("#00E676", style=QtCore.Qt.PenStyle.DashLine, width=2),
        )
        self.anchorLine.setVisible(False)
        self.plot.addItem(self.anchorLine, ignoreBounds=True)

        self.label = pg.TextItem(anchor=(0, 0), color="#FFF")
        self.plot.addItem(self.label, ignoreBounds=True)

        # --- Events ---
        scene = self.plot.scene()
        scene.sigMouseMoved.connect(self.mouse_moved_handler)
        scene.sigMouseClicked.connect(self.on_mouse_clicked)
        self.plot.sigRangeChanged.connect(self.update_hud_position)

    @QtCore.pyqtSlot(dict)
    def configure_signals(self, signals_cfg: SignalsConfig) -> None:
        """
        Recreates curves on the single shared plot.
        IGNORES min/max/range settings completely.
        """
        # 1. CLEANUP
        self.plot.clear()
        self.signal_views.clear()

        # Restore tools
        self.plot.addItem(self.vLine, ignoreBounds=True)
        self.plot.addItem(self.anchorLine, ignoreBounds=True)
        self.plot.addItem(self.label, ignoreBounds=True)

        # 2. REBUILD
        for sid, sig in signals_cfg.items():
            # Line style
            line_cfg = sig.get("line", {})
            style = line_cfg.get("style", "solid")
            pen_style = QtCore.Qt.PenStyle.SolidLine
            if style == "dashed":
                pen_style = QtCore.Qt.PenStyle.DashLine
            elif style == "dotted":
                pen_style = QtCore.Qt.PenStyle.DotLine

            # Create curve directly in the main window
            color = sig.get("color", "#FFFFFF")
            width = line_cfg.get("width", 2)
            c = pg.PlotDataItem(
                pen=pg.mkPen(color=color, width=width, style=pen_style),
                name=sig.get("label", sid),
                skipFiniteCheck=True,
            )

            # Visibility
            c.setVisible(sig.get("visible", True))
            self.plot.addItem(c)

            self.signal_views[sid] = {"curve": c, "config": sig}

    def _compute_y_bounds(
        self,
        signals: dict[str, np.ndarray],
        signal_bounds: dict[str, tuple[float, float]],
    ) -> tuple[float, float]:
        """Compute Y-axis bounds using pre-computed per-signal (min, max) from the worker thread.

        The heavy nanmin/nanmax scans already ran on the worker; here we only do an
        O(num_signals) visibility filter — no per-sample iteration on the main thread.
        """
        lo = float("inf")
        hi = float("-inf")

        for sid in signals:
            if sid not in self.signal_views:
                continue
            if not self.signal_views[sid]["curve"].isVisible():
                continue
            if sid in signal_bounds:
                # Use pre-computed bounds from the worker — O(1) lookup
                s_min, s_max = signal_bounds[sid]
            else:
                # Fallback path (e.g. during tests that don't provide signal_bounds)
                arr = signals[sid]
                if not len(arr):
                    continue
                s_min = float(np.nanmin(arr))
                s_max = float(np.nanmax(arr))
            lo = min(lo, s_min)
            hi = max(hi, s_max)

        if not np.isfinite(lo) or not np.isfinite(hi):
            return -1.0, 1.0

        return lo, hi

    @QtCore.pyqtSlot(dict)
    def on_data_ready(self, packet: PlotPacketWithRaw) -> None:
        """
        Takes RAW floats from packet and puts them on the chart.
        NO MATH HERE.
        """
        # --- FRAME SKIP GUARD ---
        now = time.perf_counter()

        if self._render_busy:
            return

        if (now - self._last_render_ts) < self._min_render_interval:
            return

        self._render_busy = True
        self._last_render_ts = now

        try:
            self.last_packet = packet
            if self.mode == PlotMode.ANALYSIS:
                return

            time_arr = packet["time"]
            # Get raw signals (floats)
            signals_data = packet["signals"]

            if len(time_arr) == 0:
                return

            for sid, raw_y in signals_data.items():
                if sid in self.signal_views:
                    # RAW_Y goes to setData directly. No subtraction, no division.
                    self.signal_views[sid]["curve"].setData(time_arr, raw_y, clear=False)

            # Throttle range updates for better performance
            now = time.perf_counter()
            should_update_ranges = (now - self._last_range_update_ts) >= self._range_update_interval

            if should_update_ranges:
                # --- X AXIS (TIME) ---
                self.plot.setXRange(time_arr[0], time_arr[-1], padding=0)

                # --- Y AXIS (ANCHOR ZERO, NO DANCING) ---
                signal_bounds: dict[str, tuple[float, float]] = packet.get("signal_bounds", {})
                y_min, y_max = self._compute_y_bounds(signals_data, signal_bounds)

                span = max(abs(y_min), abs(y_max), 1e-6)
                pad = 0.1 * span

                lo = min(y_min - pad, -pad)
                hi = max(y_max + pad, pad)

                self.plot.setYRange(lo, hi, padding=0)
                self._last_range_update_ts = now

            self.update_hud_position()
        finally:
            self._render_busy = False

    @QtCore.pyqtSlot(bool)
    def set_paused(self, paused: bool) -> None:
        if paused:
            self.mode = PlotMode.ANALYSIS
            self.analysis_packet = self.last_packet  # copy.deepcopy(self.last_packet)
        else:
            self.mode = PlotMode.LIVE
            self.analysis_packet = None
            self.anchor_time = None
            self.anchorLine.setVisible(False)
            self.label.setHtml("")

    def mouse_moved_handler(self, pos: QtCore.QPointF) -> None:
        vb = self.plot.getViewBox()
        if vb.sceneBoundingRect().contains(pos):
            mousePoint = vb.mapSceneToView(pos)
            self._process_mouse_movement(mousePoint.x())

    def _process_mouse_movement(self, x_curr: float) -> None:
        ds = self.analysis_packet if self.mode == PlotMode.ANALYSIS else self.last_packet
        if not ds or len(ds["time"]) < 2:
            return

        t_arr = ds["time"]
        x_clamped = float(np.clip(x_curr, t_arr[0], t_arr[-1]))

        self.vLine.setPos(x_clamped)
        self.update_tooltip(x_clamped, ds)

    def on_mouse_clicked(self, evt: Any) -> None:
        if self.mode != PlotMode.ANALYSIS:
            return
        if evt.button() != QtCore.Qt.MouseButton.LeftButton:
            return

        vb = self.plot.getViewBox()
        if vb.sceneBoundingRect().contains(evt.scenePos()):
            mousePoint = vb.mapSceneToView(evt.scenePos())
            t_stamp = mousePoint.x()

            self.anchor_time = t_stamp
            self.anchorLine.setPos(t_stamp)
            self.anchorLine.setVisible(True)
            self._capture_anchor_values(t_stamp)
            self._process_mouse_movement(t_stamp)

    def _capture_anchor_values(self, t_anchor: float) -> None:
        if not self.analysis_packet:
            return
        t_arr = self.analysis_packet["time"]
        raw_map = self.analysis_packet["signals"]

        self.anchor_values = {
            sid: float(np.interp(t_anchor, t_arr, vals)) for sid, vals in raw_map.items()
        }

    def update_hud_position(self) -> None:
        vb = self.plot.getViewBox()
        ranges = vb.viewRange()
        xr, yr = ranges[0], ranges[1]
        x_pos = xr[0] + 0.01 * (xr[1] - xr[0])
        y_pos = yr[1] - 0.02 * (yr[1] - yr[0])
        self.label.setPos(x_pos, y_pos)

    def update_tooltip(self, cur_t: float, ds: PlotPacketWithRaw) -> None:
        """Update tooltip with optimized interpolation."""
        t_arr = ds["time"]
        raw_map = ds["signals"]

        if len(t_arr) < 2:
            return

        html = (
            '<div style="background-color: rgba(0, 0, 0, 0.7); padding: 6px;'
            ' font-family: monospace; border: 1px solid #444;">'
        )
        html += f'<b style="color: white;">T: {cur_t:.3f} s</b>'
        if self.anchor_time is not None:
            dt = cur_t - self.anchor_time
            html += f' <span style="color: #00E676;">(Δ {dt:+.3f} s)</span>'
        html += "<br><hr style='margin: 4px 0; border: 0; border-top: 1px solid #555;'>"

        t_clamped = float(np.clip(cur_t, t_arr[0], t_arr[-1]))

        # Compute the insertion index once with a single O(log n) binary search, then reuse
        # the result for every signal — avoids calling np.interp (which runs searchsorted
        # internally) once per signal per mouse event.
        right_idx = int(np.searchsorted(t_arr, t_clamped, side="right"))
        right_idx = max(1, min(right_idx, len(t_arr) - 1))
        t_lo = float(t_arr[right_idx - 1])
        t_hi = float(t_arr[right_idx])
        frac = (t_clamped - t_lo) / (t_hi - t_lo) if t_hi != t_lo else 0.0

        sorted_sids = sorted(raw_map.keys())
        for sid in sorted_sids:
            if sid not in self.signal_views:
                continue
            view_data = self.signal_views[sid]
            if not view_data["curve"].isVisible():
                continue

            vals = raw_map[sid]
            if len(vals) < 2:
                continue

            # Linear interpolation reusing the pre-computed index and fraction
            v0, v1 = float(vals[right_idx - 1]), float(vals[right_idx])
            v = v0 + frac * (v1 - v0)
            color = view_data["config"]["color"]
            label = view_data["config"].get("label", sid)

            row = f'<span style="color: {color};">{label}: <b>{v:+.3f}</b>'
            if self.anchor_time is not None and sid in self.anchor_values:
                d_val = v - self.anchor_values[sid]
                row += f' <span style="color: #aaa; font-size: smaller;">(Δ {d_val:+.3f})</span>'
            html += row + "</span><br>"

        self.label.setHtml(html + "</div>")
        self.update_hud_position()

    @QtCore.pyqtSlot(str, bool)
    def set_signal_visible(self, sig_id: str, visible: bool) -> None:
        if sig_id in self.signal_views:
            self.signal_views[sig_id]["curve"].setVisible(visible)
