"""
Telemetry plot: stacked lanes on one shared time axis (R3.1-R3.3, review A4, P7).

Each lane is a `pg.PlotItem` with its own Y axis and range mode (`lanes.py`). The lanes'
X axes are linked, and their left axes have one fixed width so time lines up across
lanes. A lane is shown only while at least one of its signals is visible.

Live mode draws a min/max-decimated copy of the snapshot, about three points per pixel
(`series.decimate`, R3.4). The full-resolution snapshot is kept for the cursor readout.
Analysis (paused) mode draws the frozen full-resolution data, so zooming shows every
sample (pyqtgraph's own downsampling and clipping handle that).

Y ranges: X always follows the newest data in live mode. Each lane's Y is set by its mode.
Dragging or zooming a lane's Y switches that lane to manual, so the next frame doesn't
undo it. The lane's context menu (right click) switches it back.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any, TypedDict

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore, QtGui, QtWidgets

from core.types import (
    PlotMode,
    PlotPacketWithBounds,
    SignalsConfig,
    StreamConfig,
    StreamSignalConfig,
)
from ui.charts.lanes import DEFAULT_LANE, Y_MODES, LaneSpec, lane_layout, target_y_range, union
from ui.charts.series import DrawData, Interpolator, finite_bounds, prepare_draw

DEFAULT_LINE_WIDTH = 1
AXIS_WIDTH = 64  # px: equal left-axis widths keep the lanes' time axes aligned
RANGE_UPDATE_INTERVAL_S = 0.2
MIN_BUCKETS = 300  # before the view has a real width
CURSOR_RATE_HZ = 60  # mouse-move readout updates per second (P7)
MODE_LABELS = {"auto": "Auto (fit view)", "auto-grow": "Auto-grow", "manual": "Manual"}

_PEN_STYLES = {
    "solid": QtCore.Qt.PenStyle.SolidLine,
    "dashed": QtCore.Qt.PenStyle.DashLine,
    "dotted": QtCore.Qt.PenStyle.DotLine,
}


class _SignalView(TypedDict):
    curve: pg.PlotDataItem
    config: StreamSignalConfig
    lane: str
    visible: bool  # the user's choice; curve.isVisible() is also False in a hidden lane


class Lane:
    """One stacked plot: its curves' Y axis, range mode, cursor lines and readout."""

    def __init__(self, spec: LaneSpec, owner: TelemetryPlot) -> None:
        self.spec = spec
        self.key = spec.key
        self.mode = spec.mode
        self.include_zero = spec.include_zero
        self.grown: tuple[float, float] | None = None
        self._owner = owner
        # A manual lane without configured bounds takes its first data range, then holds.
        self._manual_set = spec.manual is not None

        self.plot = pg.PlotItem()
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        for name in ("left", "bottom"):
            axis = self.plot.getAxis(name)
            # Grid lines at major ticks only: all three tick levels cost ~70 ms of paint
            # per frame at 4 lanes (software raster), major only ~7 ms (R3.4).
            axis.setStyle(maxTickLevel=0)
            # Values as sent: no "(x0.001)" rescaling next to a label that has units.
            axis.enableAutoSIPrefix(False)
        # auto=True is required: mode alone leaves downsampling disabled (ds=1) (P1).
        self.plot.setDownsampling(auto=True, mode="peak")
        self.plot.setClipToView(True)
        self.plot.getAxis("left").setWidth(AXIS_WIDTH)
        self.plot.hideButtons()  # pyqtgraph's "A" auto-range would fight the lane's mode
        if spec.label:
            self.plot.setLabel("left", spec.label)
        self.vb: pg.ViewBox = self.plot.getViewBox()
        self.vb.disableAutoRange()
        if spec.manual is not None:
            self.vb.setYRange(*spec.manual, padding=0)

        self.cursor = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#888", width=1))
        self.cursor.setVisible(False)  # until the mouse is over the plot (like the HUD)
        self.anchor = pg.InfiniteLine(
            angle=90,
            movable=True,
            pen=pg.mkPen("#00E676", style=QtCore.Qt.PenStyle.DashLine, width=2),
        )
        self.anchor.setVisible(False)
        self.hud = pg.TextItem(anchor=(0, 0), color="#FFF")
        # Hidden while empty: a visible TextItem re-sets its transform in paint() whenever
        # the view's scale changes (every live frame), which schedules a second paint.
        # That doubled the paint cost per frame (R3.4).
        self.hud.setVisible(False)
        for item in (self.cursor, self.anchor, self.hud):
            self.plot.addItem(item, ignoreBounds=True)

        self.anchor.sigPositionChanged.connect(lambda line: owner.set_anchor(line.value()))
        self.vb.sigRangeChangedManually.connect(self._on_manual_range)
        self.vb.sigRangeChanged.connect(lambda *_: self.place_hud())
        self._build_menu()

    # --- range mode ---

    def _build_menu(self) -> None:
        menu: QtWidgets.QMenu | None = self.vb.menu
        if menu is None:
            return
        menu.addSeparator()
        sub = menu.addMenu("Lane Y range")
        assert sub is not None
        group = QtGui.QActionGroup(sub)
        self._mode_actions: dict[str, QtGui.QAction] = {}
        for mode in Y_MODES:
            action = sub.addAction(MODE_LABELS[mode])
            assert action is not None
            action.setCheckable(True)
            group.addAction(action)
            action.triggered.connect(lambda _=False, m=mode: self.set_mode(m))
            self._mode_actions[mode] = action
        sub.addSeparator()
        zero = sub.addAction("Include zero")
        assert zero is not None
        zero.setCheckable(True)
        zero.toggled.connect(self.set_include_zero)
        self._zero_action = zero
        self._sync_menu()

    def _sync_menu(self) -> None:
        if hasattr(self, "_mode_actions"):
            self._mode_actions[self.mode].setChecked(True)
            self._zero_action.blockSignals(True)
            self._zero_action.setChecked(self.include_zero)
            self._zero_action.blockSignals(False)

    def set_mode(self, mode: str) -> None:
        if mode not in Y_MODES:
            raise ValueError(f"unknown Y range mode {mode!r}")
        self.mode = mode
        self.grown = None
        self._manual_set = True  # entering manual keeps what is on screen
        self._sync_menu()
        self._owner.refresh_ranges()

    def set_include_zero(self, include: bool) -> None:
        self.include_zero = include
        self._sync_menu()
        self._owner.refresh_ranges()

    def _on_manual_range(self, mask: Any) -> None:
        """The user dragged or zoomed Y: hold that range instead of re-fitting over it."""
        if isinstance(mask, list | tuple) and len(mask) > 1 and mask[1] and self.mode != "manual":
            self.mode = "manual"
            self._manual_set = True
            self._sync_menu()

    def apply(self, data: tuple[float, float] | None) -> None:
        """Sets the Y range for the data currently in view, as the mode says."""
        if self.mode == "auto-grow":
            self.grown = union(self.grown, data)
        if self.mode == "manual":
            if self._manual_set or data is None:
                return
            self._manual_set = True
            target = target_y_range("auto", data, None, self.include_zero)
        else:
            target = target_y_range(self.mode, data, self.grown, self.include_zero)
        if target is not None:
            self.vb.setYRange(*target, padding=0)

    def set_hud(self, rows: list[str]) -> None:
        """Shows the readout rows (HTML); hides the HUD when there are none."""
        if not rows:
            self.hud.setHtml("")
            self.hud.setVisible(False)
            return
        self.hud.setHtml(
            '<div style="background-color: rgba(0, 0, 0, 0.7); padding: 4px;'
            ' font-family: monospace;">' + "<br>".join(rows) + "</div>"
        )
        self.hud.setVisible(True)
        self.place_hud()

    def place_hud(self) -> None:
        (x0, x1), (y0, y1) = self.vb.viewRange()
        self.hud.setPos(x0 + 0.01 * (x1 - x0), y1 - 0.02 * (y1 - y0))


class TelemetryPlot(QtWidgets.QWidget):
    cursor_moved = QtCore.pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.mode: PlotMode = PlotMode.LIVE
        self.signal_views: dict[str, _SignalView] = {}
        self.lanes: dict[str, Lane] = {}
        self._shown: list[str] = []

        # The latest live packet: min/max buckets for large windows (see value_source).
        self.last_packet: PlotPacketWithBounds | None = None
        self.analysis_packet: PlotPacketWithBounds | None = None
        self.anchor_time: float | None = None
        self.anchor_values: dict[str, float] = {}
        self._cursor_x: float | None = None
        # Exact values at a time, for the live readout (live frames hold min/max buckets):
        # (t, signal ids) -> {signal id: value}. Set by LiveFeed to the store's values_at.
        self.value_source: Callable[[float, list[str]], dict[str, float]] | None = None
        self._last_range_ts = 0.0
        self._ranges_due = True
        self._syncing_anchor = False
        # Overlaid traces of a previous capture (R4.5), dimmed and dashed.
        self._reference: list[tuple[Lane, pg.PlotDataItem]] = []

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.graphics = pg.GraphicsLayoutWidget()
        layout.addWidget(self.graphics)

        scene = self.graphics.scene()
        # Rate-limited: a readout per raw mouse event re-rendered the HUD far too often (P7).
        self._mouse_proxy = pg.SignalProxy(
            scene.sigMouseMoved,
            rateLimit=CURSOR_RATE_HZ,
            slot=self._on_mouse_moved,
            threadSafe=False,  # GUI thread only: a plain QTimer that can be parented
        )
        # Parentless, the proxy and its timer would be freed by whichever thread's garbage
        # collection finds them, possibly a reader/engine thread. That leaves the timer
        # registered on the GUI thread, and its next event hits freed memory. Parented, Qt
        # destroys them with the plot, on the GUI thread.
        self._mouse_proxy.setParent(self)
        self._mouse_proxy.timer.setParent(self._mouse_proxy)
        scene.sigMouseClicked.connect(self.on_mouse_clicked)
        self.configure_stream({"signals": {}})

    # --- configuration ---

    @property
    def plot(self) -> pg.PlotItem:
        """The top shown lane's plot (the one carrying the time readout)."""
        return self.lanes[self._shown[0]].plot

    def shown_lanes(self) -> list[str]:
        return list(self._shown)

    def configure_signals(
        self, signals_cfg: SignalsConfig, groups: dict[str, Any] | None = None
    ) -> None:
        self.configure_stream({"signals": signals_cfg, "groups": groups or {}})

    def configure_stream(self, stream_cfg: StreamConfig | dict[str, Any]) -> None:
        """Rebuilds lanes and curves for a stream's signals and `groups`."""
        # Every lane stays in the layout (hidden ones collapse), so lane plots always
        # live and die with the scene. A plot taken out of the scene outlives it, and
        # destroying it later crashed Qt.
        for lane in self.lanes.values():
            lane.plot.setXLink(None)
        self.graphics.clear()
        self._reference = []
        self._shown = []
        self.lanes.clear()
        self.signal_views.clear()

        specs, assignment = lane_layout(stream_cfg)
        if not specs:
            specs = [LaneSpec(DEFAULT_LANE, "", 0.0)]
        for spec in specs:
            self._add_lane(spec)

        raw_signals: Any = stream_cfg.get("signals")
        signals: dict[str, StreamSignalConfig] = (
            raw_signals if isinstance(raw_signals, dict) else {}
        )
        for sid, sig in signals.items():
            line_cfg = sig.get("line", {})
            curve = pg.PlotDataItem(
                # 1 px is the fast path in Qt's raster engine; wider pens are much slower (P4).
                pen=pg.mkPen(
                    color=sig.get("color", "#FFFFFF"),
                    width=line_cfg.get("width", DEFAULT_LINE_WIDTH),
                    style=_PEN_STYLES.get(line_cfg.get("style", "solid"), _PEN_STYLES["solid"]),
                ),
                name=sig.get("label", sid),
                # Default connect="auto": NaN (a gap) breaks the line instead of being drawn.
                # Don't set skipFiniteCheck, which assumes no NaN.
            )
            visible = bool(sig.get("visible", True))
            curve.setVisible(visible)
            lane_key = assignment[sid]
            self.lanes[lane_key].plot.addItem(curve)
            self.signal_views[sid] = {
                "curve": curve,
                "config": sig,
                "lane": lane_key,
                "visible": visible,
            }

        self.anchor_time = None
        self.anchor_values = {}
        self._relayout()

    def _add_lane(self, spec: LaneSpec) -> Lane:
        lane = Lane(spec, self)
        self.graphics.addItem(lane.plot, row=len(self.lanes), col=0)
        self.lanes[spec.key] = lane
        lane.vb.sigXRangeChanged.connect(lambda *_, k=spec.key: self._on_x_range_changed(k))
        return lane

    def _lane_of_visible(self) -> set[str]:
        return {v["lane"] for v in self.signal_views.values() if v["visible"]}

    def _relayout(self) -> None:
        """Shows the lanes with a visible signal (at least one lane), top to bottom."""
        used = self._lane_of_visible()
        order = [k for k in self.lanes if k in used] or [next(iter(self.lanes))]
        self._shown = order
        master = self.lanes[order[0]].plot
        master.setXLink(None)
        for key, lane in self.lanes.items():
            lane.plot.setVisible(key in order)
            if lane.plot is not master:
                lane.plot.setXLink(master)
            last = key == order[-1]
            lane.plot.getAxis("bottom").setStyle(showValues=last)
            lane.plot.setLabel("bottom", "Time [s]" if last else None)
        self._apply_mouse_mode()
        self.refresh_ranges()

    def _apply_mouse_mode(self) -> None:
        live = self.mode == PlotMode.LIVE
        for lane in self.lanes.values():
            # Live: X follows the newest data, so only Y is the user's to zoom and pan.
            lane.vb.setMouseEnabled(x=not live, y=True)
            # Live frames arrive decimated to ~the pixel width. pyqtgraph's own clipping and
            # auto-downsampling only add cost there (~3 ms per frame at 34 signals). Paused,
            # they keep zooming into the full-resolution capture fast (P1).
            lane.plot.setDownsampling(auto=not live, mode="peak")
            lane.plot.setClipToView(not live)

    @QtCore.pyqtSlot(str, bool)
    def set_signal_visible(self, sig_id: str, visible: bool) -> None:
        if sig_id in self.signal_views:
            self.signal_views[sig_id]["visible"] = visible
            self.signal_views[sig_id]["curve"].setVisible(visible)
            self._relayout()

    def move_signal(self, sig_id: str, lane_key: str, label: str | None = None) -> None:
        """Moves a signal to another lane, creating the lane if needed (session only)."""
        view = self.signal_views.get(sig_id)
        if view is None or view["lane"] == lane_key:
            return
        if lane_key not in self.lanes:
            order = max((lane.spec.order for lane in self.lanes.values()), default=0.0) + 1
            self._add_lane(LaneSpec(lane_key, label or lane_key, order))
        self.lanes[view["lane"]].plot.removeItem(view["curve"])
        self.lanes[lane_key].plot.addItem(view["curve"])
        view["lane"] = lane_key
        self._relayout()

    def set_lane_mode(self, lane_key: str, mode: str) -> None:
        self.lanes[lane_key].set_mode(mode)

    def visible_signal_ids(self) -> list[str]:
        """The signals worth copying out of the store for the next frame."""
        return [sid for sid, view in self.signal_views.items() if view["visible"]]

    # --- drawing ---

    def draw_buckets(self) -> int:
        """How many time buckets a live frame is decimated to: one per pixel of width."""
        return max(int(self.lanes[self._shown[0]].vb.width()), MIN_BUCKETS)

    def show_packet(self, packet: PlotPacketWithBounds, prepared: DrawData | None = None) -> None:
        """
        Draws a live packet (the visible signals). Called by LiveFeed at frame rate, with
        the decimation already `prepared` off the GUI thread. Ignored while paused.
        """
        self.last_packet = packet
        if self.mode == PlotMode.ANALYSIS:
            return
        t = packet["time"]
        if len(t) == 0:
            return
        draw = prepared or prepare_draw(t, packet["signals"], self.draw_buckets())
        self._draw(draw.time, draw.signals)
        self.plot.setXRange(float(t[0]), float(t[-1]), padding=0)

        now = time.perf_counter()
        if self._ranges_due or now - self._last_range_ts >= RANGE_UPDATE_INTERVAL_S:
            self._apply_ranges(packet.get("signal_bounds") or draw.bounds, packet)
            self._last_range_ts = now
            self._ranges_due = False
        # Apply the new ranges now. pyqtgraph otherwise defers this to paint, where moving
        # the view schedules a second paint: two paints per live frame (R3.4).
        for key in self._shown:
            self.lanes[key].vb.updateMatrix()

    def _draw(self, t: np.ndarray, signals: dict[str, np.ndarray]) -> None:
        for sid, y in signals.items():
            view = self.signal_views.get(sid)
            if view is not None:
                view["curve"].setData(t, y)

    def refresh_ranges(self) -> None:
        """Re-applies every lane's range at the next opportunity (now, if paused)."""
        self._ranges_due = True
        if self.mode == PlotMode.ANALYSIS and self.analysis_packet is not None:
            self._apply_ranges({}, self.analysis_packet, in_view=True)

    def _apply_ranges(
        self,
        bounds: dict[str, tuple[float, float]],
        packet: PlotPacketWithBounds,
        in_view: bool = False,
    ) -> None:
        """
        Each shown lane fits the visible signals it holds. Live: the snapshot's bounds
        (the whole window is in view). Paused: only the samples inside the X range.
        """
        x_range = tuple(self.plot.viewRange()[0]) if in_view else None
        for key in self._shown:
            lane = self.lanes[key]
            data: tuple[float, float] | None = None
            for sid, view in self.signal_views.items():
                if view["lane"] != key or not view["visible"]:
                    continue
                y = packet["signals"].get(sid)
                if y is None:
                    continue
                if not in_view and sid in bounds:
                    b: tuple[float, float] | None = bounds[sid]
                else:
                    b = finite_bounds(packet["time"], y, x_range)
                data = union(data, b)
            lane.apply(data)

    def _on_x_range_changed(self, lane_key: str) -> None:
        if lane_key != self._shown[0]:
            return  # the linked lanes follow the top one; fit once per change
        if self.mode == PlotMode.ANALYSIS and self.analysis_packet is not None:
            self._apply_ranges({}, self.analysis_packet, in_view=True)

    def set_paused(self, paused: bool, frozen: PlotPacketWithBounds | None = None) -> None:
        """
        Enters or leaves analysis mode. `frozen` should hold *all* signals (live packets
        carry only visible ones), so signals made visible while paused still have data.
        """
        if paused:
            self.mode = PlotMode.ANALYSIS
            self.analysis_packet = frozen if frozen is not None else self.last_packet
            if self.analysis_packet is not None and len(self.analysis_packet["time"]):
                self._draw(self.analysis_packet["time"], self.analysis_packet["signals"])
        else:
            self.mode = PlotMode.LIVE
            self.analysis_packet = None
            self.anchor_time = None
            self.anchor_values = {}
            for lane in self.lanes.values():
                lane.anchor.setVisible(False)
                lane.set_hud([])
            self.clear_reference()
        self._apply_mouse_mode()
        self.refresh_ranges()

    # --- reference overlay (R4.5) ---

    def set_reference(self, packet: PlotPacketWithBounds, shift_s: float) -> None:
        """
        Overlays another capture's visible signals, shifted by `shift_s` in time (to line up
        two triggers), as dimmed dashed traces in their lanes. Cleared when live view resumes.
        """
        self.clear_reference()
        t = packet["time"] + shift_s
        for sid, y in packet["signals"].items():
            view = self.signal_views.get(sid)
            if view is None or not view["visible"]:
                continue
            color = QtGui.QColor(view["config"].get("color", "#FFFFFF"))
            color.setAlpha(120)
            item = pg.PlotDataItem(
                t, y, pen=pg.mkPen(color, width=1, style=QtCore.Qt.PenStyle.DashLine)
            )
            lane = self.lanes[view["lane"]]
            lane.plot.addItem(item)
            self._reference.append((lane, item))

    def clear_reference(self) -> None:
        for lane, item in self._reference:
            lane.plot.removeItem(item)
        self._reference = []

    def reference_count(self) -> int:
        return len(self._reference)

    # --- cursor and readout (R3.3) ---

    def _on_mouse_moved(self, event: Any) -> None:
        pos = event[0] if isinstance(event, tuple | list) else event
        for key in self._shown:
            vb = self.lanes[key].vb
            if vb.sceneBoundingRect().contains(pos):
                self.move_cursor(vb.mapSceneToView(pos).x())
                return

    def move_cursor(self, x: float) -> None:
        ds = self.analysis_packet if self.mode == PlotMode.ANALYSIS else self.last_packet
        if ds is None or len(ds["time"]) < 2:
            return
        t = ds["time"]
        x = float(np.clip(x, t[0], t[-1]))
        self._cursor_x = x
        for key in self._shown:
            self.lanes[key].cursor.setPos(x)
            self.lanes[key].cursor.setVisible(True)
        self._update_readout(x, ds)

    def on_mouse_clicked(self, evt: Any) -> None:
        """In analysis mode, a left click drops the Δ anchor (it can be dragged later)."""
        if self.mode != PlotMode.ANALYSIS or evt.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        for key in self._shown:
            vb = self.lanes[key].vb
            if vb.sceneBoundingRect().contains(evt.scenePos()):
                self.set_anchor(vb.mapSceneToView(evt.scenePos()).x())
                return

    def set_anchor(self, t_anchor: float) -> None:
        """Places the Δ reference in every lane and captures the values there."""
        if self._syncing_anchor or self.analysis_packet is None:
            return
        self._syncing_anchor = True
        try:
            self.anchor_time = float(t_anchor)
            for lane in self.lanes.values():
                lane.anchor.setPos(self.anchor_time)
                lane.anchor.setVisible(True)
            ds = self.analysis_packet
            interp = Interpolator(ds["time"], self.anchor_time)
            self.anchor_values = {sid: interp.value(y) for sid, y in ds["signals"].items()}
        finally:
            self._syncing_anchor = False
        self.move_cursor(self._cursor_x if self._cursor_x is not None else self.anchor_time)

    def _update_readout(self, x: float, ds: PlotPacketWithBounds) -> None:
        values: dict[str, float]
        if self.mode == PlotMode.LIVE and self.value_source is not None:
            values = self.value_source(x, self.visible_signal_ids())
        else:
            interp = Interpolator(ds["time"], x)
            values = {sid: interp.value(y) for sid, y in ds["signals"].items()}
        header = f"T = {x:.3f} s"
        if self.anchor_time is not None:
            header += f"  (Δt {x - self.anchor_time:+.3f} s)"
        self.cursor_moved.emit(header)
        for i, key in enumerate(self._shown):
            lane = self.lanes[key]
            rows: list[str] = []
            if i == 0:
                rows.append(f'<b style="color: white;">{header}</b>')
            for sid, view in self.signal_views.items():
                if view["lane"] != key or not view["visible"] or sid not in values:
                    continue
                cfg = view["config"]
                color, label = cfg.get("color", "#FFFFFF"), cfg.get("label", sid)
                v = values[sid]
                if not math.isfinite(v):
                    rows.append(f'<span style="color: {color};">{label}: <b>n/a</b></span>')
                    continue
                row = f'<span style="color: {color};">{label}: <b>{v:+.3f}</b>'
                ref = (
                    self.anchor_values.get(sid, math.nan)
                    if self.anchor_time is not None
                    else math.nan
                )
                if math.isfinite(ref):
                    row += f' <span style="color: #aaa;">(Δ {v - ref:+.3f})</span>'
                rows.append(row + "</span>")
            lane.set_hud(rows)

    def readout_text(self) -> str:
        """The readout of every shown lane as plain text (top to bottom)."""
        return "\n".join(self.lanes[key].hud.textItem.toPlainText() for key in self._shown)
