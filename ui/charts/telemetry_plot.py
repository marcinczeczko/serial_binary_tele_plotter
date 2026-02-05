"""
Telemetry Plotting Module (Absolute Raw Mode).
No normalization. No scaling math. No ViewBox stacking.
What comes in packet['signals'] is displayed exactly on the Y-axis.
"""

import time
from typing import Any, Dict, Optional

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore, QtWidgets

from core.types import PlotMode


class TelemetryPlot(QtWidgets.QWidget):
    cursor_moved = QtCore.pyqtSignal(str)

    def __init__(self):
        super().__init__()

        # --- State ---
        self.mode = PlotMode.LIVE

        self._render_busy = False
        self._last_render_ts = 0.0
        self._min_render_interval = 0.07
        
        # Performance: Throttle range updates
        self._last_range_update_ts = 0.0
        self._range_update_interval = 0.2  # Update ranges every 200ms instead of every frame

        # signal_views: trzymamy tu tylko referencje do krzywych
        # Format: { "signal_id": { "curve": pg.PlotDataItem, "config": dict } }
        self.signal_views: Dict[str, Dict[str, Any]] = {}

        self.last_packet: Optional[dict] = None
        self.analysis_packet: Optional[dict] = None

        self.anchor_time: Optional[float] = None
        self.anchor_values: Dict[str, float] = {}

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

        # WŁĄCZAMY lewą oś Y i włączamy auto-skalowanie
        self.plot.getAxis("left").setVisible(True)
        self.plot.enableAutoRange(axis="y")
        # Opcjonalnie: zablokuj minimalny zakres, żeby nie szalało przy zerach
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
    def configure_signals(self, signals_cfg: dict):
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
            # Styl linii
            style = sig["line"].get("style", "solid")
            pen_style = QtCore.Qt.PenStyle.SolidLine
            if style == "dashed":
                pen_style = QtCore.Qt.PenStyle.DashLine
            elif style == "dotted":
                pen_style = QtCore.Qt.PenStyle.DotLine

            # Tworzymy krzywą bezpośrednio w głównym oknie
            c = pg.PlotDataItem(
                pen=pg.mkPen(color=sig["color"], width=sig["line"]["width"], style=pen_style),
                name=sig.get("label", sid),
                skipFiniteCheck=True,
            )

            # Widoczność
            c.setVisible(sig.get("visible", True))
            self.plot.addItem(c)

            self.signal_views[sid] = {"curve": c, "config": sig}

    def _compute_y_bounds(self, signals: dict) -> tuple[float, float]:
        """Compute Y-axis bounds efficiently, only checking visible signals."""
        lo = float("inf")
        hi = float("-inf")

        # Only compute bounds for visible signals
        for sid, arr in signals.items():
            if sid not in self.signal_views:
                continue
            if not self.signal_views[sid]["curve"].isVisible():
                continue
            if len(arr):
                # Use numpy's nanmin/nanmax for safety, but faster than min/max on large arrays
                arr_min = float(np.nanmin(arr))
                arr_max = float(np.nanmax(arr))
                lo = min(lo, arr_min)
                hi = max(hi, arr_max)

        if not np.isfinite(lo) or not np.isfinite(hi):
            return -1.0, 1.0

        return lo, hi

    @QtCore.pyqtSlot(dict)
    def on_data_ready(self, packet: dict):
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
            # Pobieramy SUROWE sygnały (floats)
            signals_data = packet["signals"]

            if len(time_arr) == 0:
                return

            for sid, raw_y in signals_data.items():
                if sid in self.signal_views:
                    # RAW_Y idzie prosto do setData. Bez odejmowania, bez dzielenia.
                    self.signal_views[sid]["curve"].setData(time_arr, raw_y, clear=False)

            # Throttle range updates for better performance
            now = time.perf_counter()
            should_update_ranges = (now - self._last_range_update_ts) >= self._range_update_interval
            
            if should_update_ranges:
                # Przesuwanie osi X (czas)
                self.plot.setXRange(time_arr[0], time_arr[-1], padding=0)

                # --- Y AXIS (ANCHOR ZERO, NO DANCING) ---
                y_min, y_max = self._compute_y_bounds(signals_data)

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
    def set_paused(self, paused: bool):
        if paused:
            self.mode = PlotMode.ANALYSIS
            self.analysis_packet = self.last_packet  # copy.deepcopy(self.last_packet)
        else:
            self.mode = PlotMode.LIVE
            self.analysis_packet = None
            self.anchor_time = None
            self.anchorLine.setVisible(False)
            self.label.setHtml("")

    def mouse_moved_handler(self, pos: QtCore.QPointF):
        vb = self.plot.getViewBox()
        if vb.sceneBoundingRect().contains(pos):
            mousePoint = vb.mapSceneToView(pos)
            self._process_mouse_movement(mousePoint.x())

    def _process_mouse_movement(self, x_curr: float):
        ds = self.analysis_packet if self.mode == PlotMode.ANALYSIS else self.last_packet
        if not ds or len(ds["time"]) < 2:
            return

        t_arr = ds["time"]
        x_clamped = float(np.clip(x_curr, t_arr[0], t_arr[-1]))

        self.vLine.setPos(x_clamped)
        self.update_tooltip(x_clamped, ds)

    def on_mouse_clicked(self, evt):
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

    def _capture_anchor_values(self, t_anchor: float):
        if not self.analysis_packet:
            return
        t_arr = self.analysis_packet["time"]
        raw_map = self.analysis_packet["signals"]

        self.anchor_values = {
            sid: float(np.interp(t_anchor, t_arr, vals)) for sid, vals in raw_map.items()
        }

    def update_hud_position(self):
        vb = self.plot.getViewBox()
        ranges = vb.viewRange()
        xr, yr = ranges[0], ranges[1]
        x_pos = xr[0] + 0.01 * (xr[1] - xr[0])
        y_pos = yr[1] - 0.02 * (yr[1] - yr[0])
        self.label.setPos(x_pos, y_pos)

    def update_tooltip(self, cur_t: float, ds: dict):
        """Update tooltip with optimized interpolation."""
        t_arr = ds["time"]
        raw_map = ds["signals"]
        
        # Early exit if no data
        if len(t_arr) < 2:
            return

        html = f'<div style="background-color: rgba(0, 0, 0, 0.7); padding: 6px; font-family: monospace; border: 1px solid #444;">'
        html += f'<b style="color: white;">T: {cur_t:.3f} s</b>'
        if self.anchor_time:
            dt = cur_t - self.anchor_time
            html += f' <span style="color: #00E676;">(Δ {dt:+.3f} s)</span>'
        html += "<br><hr style='margin: 4px 0; border: 0; border-top: 1px solid #555;'>"

        # Pre-clip time to valid range to avoid repeated checks
        t_clamped = float(np.clip(cur_t, t_arr[0], t_arr[-1]))
        
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

            # Use pre-clipped time value
            v = float(np.interp(t_clamped, t_arr, vals))
            color = view_data["config"]["color"]
            label = view_data["config"].get("label", sid)

            row = f'<span style="color: {color};">{label}: <b>{v:+.3f}</b>'
            if self.anchor_time and sid in self.anchor_values:
                d_val = v - self.anchor_values[sid]
                row += f' <span style="color: #aaa; font-size: smaller;">(Δ {d_val:+.3f})</span>'
            html += row + "</span><br>"

        self.label.setHtml(html + "</div>")
        self.update_hud_position()

    @QtCore.pyqtSlot(str, bool)
    def set_signal_visible(self, sig_id: str, visible: bool):
        if sig_id in self.signal_views:
            self.signal_views[sig_id]["curve"].setVisible(visible)
