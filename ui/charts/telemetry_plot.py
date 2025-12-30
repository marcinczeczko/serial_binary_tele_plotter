"""
Telemetry Plotting Module.
"""

# ... importy bez zmian ...
import copy
from typing import Any, Dict, Optional

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore, QtWidgets

from core.types import PlotMode


class TelemetryPlot(QtWidgets.QWidget):
    cursor_moved = QtCore.pyqtSignal(str)

    def __init__(self):
        super().__init__()
        # ... init bez zmian ...
        self.mode: PlotMode = PlotMode.LIVE
        self.signal_views: Dict[str, Dict[str, Any]] = {}
        self.signal_colors: Dict[str, str] = {}
        self.last_packet: Optional[dict] = None
        self.analysis_packet: Optional[dict] = None
        self.anchor_time: Optional[float] = None
        self.anchor_values: Dict[str, float] = {}

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.graphics = pg.GraphicsLayoutWidget()
        layout.addWidget(self.graphics)
        self.plot: pg.PlotItem = self.graphics.addPlot()  # type: ignore

        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel("bottom", "Time [s]")
        self.plot.getAxis("left").setVisible(False)

        self.vLine = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#888", width=1))
        self.plot.addItem(self.vLine, ignoreBounds=True)
        self.anchorLine = pg.InfiniteLine(
            angle=90, movable=False, pen=pg.mkPen("y", style=QtCore.Qt.PenStyle.DashLine, width=2)
        )
        self.anchorLine.setVisible(False)
        self.plot.addItem(self.anchorLine, ignoreBounds=True)
        self.label = pg.TextItem(anchor=(0, 0))
        self.plot.addItem(self.label, ignoreBounds=True)

        scene = self.plot.scene()
        scene.sigMouseMoved.connect(self.mouse_moved_handler)  # type: ignore
        scene.sigMouseClicked.connect(self.on_mouse_clicked)  # type: ignore
        self.plot.sigRangeChanged.connect(self.update_hud_position)  # type: ignore

    @QtCore.pyqtSlot(dict)
    def on_data_ready(self, packet: dict):
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

        # Fix: ViewBox check
        vb = self.plot.getViewBox()
        if vb is not None:
            vb.setXRange(time[0], time[-1], padding=0)
        self.update_hud_position()

    # ... set_paused, mouse_moved_handler, on_mouse_clicked etc bez zmian ...
    @QtCore.pyqtSlot(bool)
    def set_paused(self, paused: bool):
        if paused:
            self.mode = PlotMode.ANALYSIS
            self.analysis_packet = copy.deepcopy(self.last_packet)
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

    def _process_mouse_movement(self, x_raw: float):
        ds = self.analysis_packet if self.mode == PlotMode.ANALYSIS else self.last_packet
        if not ds or len(ds["time"]) < 2:
            return
        x_clamped = float(np.clip(x_raw, ds["time"][0], ds["time"][-1]))
        self.vLine.setPos(x_clamped)
        self.update_tooltip(x_clamped)

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
        raw = self.analysis_packet["raw"]
        self.anchor_values = {s: np.interp(t_anchor, t_arr, v) for s, v in raw.items()}

    def update_hud_position(self):
        vb = self.plot.getViewBox()
        ranges = vb.viewRange()
        xr: list[float] = ranges[0]
        yr: list[float] = ranges[1]
        x_pos = xr[0] + 0.01 * (xr[1] - xr[0])
        y_pos = yr[1] - 0.02 * (yr[1] - yr[0])
        self.label.setPos(x_pos, y_pos)

    def update_tooltip(self, x_pos: float):
        # ... (Logika tooltipa bez zmian) ...
        ds = self.analysis_packet if self.mode == PlotMode.ANALYSIS else self.last_packet
        if not ds or len(ds["time"]) < 2:
            return
        t_arr = ds["time"]
        raw = ds["raw"]
        cur_t = float(x_pos)
        dt = t_arr[1] - t_arr[0]
        idx = int(np.clip((cur_t - t_arr[0]) / dt, 0, len(t_arr) - 1))
        self.cursor_moved.emit(f"Cursor: {cur_t:.3f} s | Index: {idx}")

        html = f'<div style="background-color: rgba(0, 0, 0, 0.7); padding: 6px; font-family: Consolas; border: 1px solid #444;">'
        html += f'<b style="color: white;">T: {cur_t:.3f} s</b>'
        if self.anchor_time:
            html += f' <span style="color: #FFD700;">(Δ {cur_t - self.anchor_time:+.3f} s)</span>'
        html += "<br><hr style='margin: 4px 0;'>"

        for sid, vals in raw.items():
            if sid in self.signal_views and self.signal_views[sid]["curve"].isVisible():
                data_len = min(len(t_arr), len(vals))
                if data_len < 2:
                    continue
                v = float(np.interp(cur_t, t_arr[:data_len], vals[:data_len]))
                color = self.signal_colors.get(sid, "#FFF")
                row = f'<span style="color: {color};">{sid}: <b>{v:>8.4f}</b>'
                if self.anchor_time and sid in self.anchor_values:
                    delta_v = v - self.anchor_values[sid]
                    row += f' <span style="color: #aaa;">(Δ {delta_v:+.4f})</span>'
                html += row + "</span><br>"
        self.label.setHtml(html + "</div>")
        self.update_hud_position()

    @QtCore.pyqtSlot(dict)
    def configure_signals(self, signals_cfg: dict):
        """Reconfigures the plot area based on the stream definition."""
        # 1. CLEANUP
        for view_info in self.signal_views.values():
            self.plot.scene().removeItem(view_info["viewbox"])
        self.signal_views.clear()
        self.signal_colors.clear()
        self.plot.clear()

        # Restore static tools
        self.plot.addItem(self.vLine, ignoreBounds=True)
        self.plot.addItem(self.anchorLine, ignoreBounds=True)
        self.plot.addItem(self.label, ignoreBounds=True)

        base_vb = self.plot.getViewBox()

        # 2. REBUILD
        for sid, sig in signals_cfg.items():
            self.signal_colors[sid] = sig["color"]

            vb = pg.ViewBox()
            vb.setMouseEnabled(x=False, y=False)
            vb.setYRange(0.0, 1.0)
            self.plot.scene().addItem(vb)
            vb.setXLink(base_vb)

            pen_style = QtCore.Qt.PenStyle.SolidLine
            if sig["line"]["style"] == "dashed":
                pen_style = QtCore.Qt.PenStyle.DashLine
            elif sig["line"]["style"] == "dotted":
                pen_style = QtCore.Qt.PenStyle.DotLine

            c = pg.PlotDataItem(
                pen=pg.mkPen(color=sig["color"], width=sig["line"]["width"], style=pen_style),
                skipFiniteCheck=True,
            )
            # Apply initial visibility
            is_visible = sig.get("visible", True)
            c.setVisible(is_visible)

            vb.addItem(c)
            self.signal_views[sid] = {"viewbox": vb, "curve": c}

        try:
            base_vb.sigResized.disconnect(self._update_views)
        except TypeError:
            pass
        base_vb.sigResized.connect(self._update_views)

        # --- CRITICAL FIX: Force geometry update immediately ---
        self._update_views()

    def _update_views(self):
        base_vb = self.plot.getViewBox()
        if base_vb is None:
            return
        rect = base_vb.sceneBoundingRect()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        for s in self.signal_views.values():
            s["viewbox"].setGeometry(rect)

    @QtCore.pyqtSlot(str, bool)
    def set_signal_visible(self, sig_id: str, visible: bool):
        if sig_id in self.signal_views:
            self.signal_views[sig_id]["curve"].setVisible(visible)
