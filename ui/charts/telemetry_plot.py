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

The cursor readout is emitted (`readout_changed`) and shown in the Signals panel, not drawn
on the plot (R6.2): a visible `TextItem` costs a second paint per frame. Live, a mouse move
waits for the next frame (R10.1): moving the cursor lines repaints every curve under them,
so applied on its own it made about 2.7 plot paints per frame. Overlays are plain
lines and regions for the same reason: command markers (R6.3), and the trigger's level line
and capture window (R6.5).

Scope look (R9.6): each lane is a framed graticule with the trigger `T` and cursor `A`/`B`
markers (`graticule.py`: painted items that never move during paint), the lane's name dim
in small caps up its Y gutter, and the time unit on the last X tick instead of a `Time [s]`
row. Cursor A (the mouse) is a solid light-grey line, B (the Δ anchor) a dashed one.
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
from ui.charts.graticule import CURSOR, FRAME, Graticule, LaneMarkers
from ui.charts.lanes import DEFAULT_LANE, Y_MODES, LaneSpec, lane_layout, target_y_range, union
from ui.charts.series import DrawData, Interpolator, Readout, finite_bounds, prepare_draw
from ui.panels.signal_rows import lane_title

DEFAULT_LINE_WIDTH = 1
AXIS_WIDTH = 64  # px: equal left-axis widths keep the lanes' time axes aligned
RANGE_UPDATE_INTERVAL_S = 0.2
MIN_BUCKETS = 300  # before the view has a real width
CURSOR_RATE_HZ = 60  # mouse-move readout updates per second (P7)
# Live, a mouse move is applied with the next frame if one came this recently (R10.1), else
# at once; if frames stop, a deferred move is applied after CURSOR_FLUSH_MS anyway.
LIVE_FRAME_GAP_S = 0.1
CURSOR_FLUSH_MS = 100
MARKER_COLOR = "#FFB000"
TRIGGER_COLOR = "#FF9A1A"
TICK_TEXT = "#8a8a8a"
LANE_NAME = "#777777"
TIME_UNIT = "s"
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


class TimeAxis(pg.AxisItem):  # type: ignore[misc]  # pyqtgraph is untyped
    """Time ticks; the last one carries the unit (`2.5 s`), so there's no `Time [s]` row."""

    def tickStrings(  # noqa: N802
        self, values: list[float], scale: float, spacing: float
    ) -> list[str]:
        strings: list[str] = super().tickStrings(values, scale, spacing)
        # The last tick whose label fits: pyqtgraph skips a label that would cross the
        # axis's end, so a unit on that one would never be seen.
        lo, hi = sorted(self.range)
        length = float(self.geometry().width())
        metrics = QtGui.QFontMetrics(self.style.get("tickFont") or self.font())
        fits = []
        for i, v in enumerate(values):
            x = (v * scale - lo) / (hi - lo) * length if hi > lo else 0.0
            half = metrics.horizontalAdvance(f"{strings[i]} {TIME_UNIT}") / 2
            if half <= x <= length - half:
                fits.append(i)
        if fits:
            last = max(fits, key=lambda i: values[i])
            strings[last] = f"{strings[last]} {TIME_UNIT}"
            for i, v in enumerate(values):  # the unit's label is the last one shown
                if v > values[last]:
                    strings[i] = ""
        return strings


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

        self.plot = pg.PlotItem(axisItems={"bottom": TimeAxis(orientation="bottom")})
        for name in ("left", "bottom"):
            axis = self.plot.getAxis(name)
            # Major ticks only: all three tick levels cost ~70 ms of paint per frame at
            # 4 lanes (software raster), major only ~7 ms (R3.4). The graticule draws the
            # grid (R9.6), not the axes.
            axis.setStyle(maxTickLevel=0)
            # Values as sent: no "(x0.001)" rescaling next to a label that has units.
            axis.enableAutoSIPrefix(False)
            axis.setPen(pg.mkPen(FRAME))
            axis.setTextPen(pg.mkPen(TICK_TEXT))
        # auto=True is required: mode alone leaves downsampling disabled (ds=1) (P1).
        self.plot.setDownsampling(auto=True, mode="peak")
        self.plot.setClipToView(True)
        self.plot.getAxis("left").setWidth(AXIS_WIDTH)
        self.plot.hideButtons()  # pyqtgraph's "A" auto-range would fight the lane's mode
        if spec.label:
            self.plot.setLabel("left", lane_title(spec.label), color=LANE_NAME, size="10px")
        self.vb: pg.ViewBox = self.plot.getViewBox()
        self.graticule = Graticule(self.vb, self._y_ticks)
        self.markers = LaneMarkers(self.vb)
        self.vb.sigRangeChanged.connect(self._on_range_changed)
        self.vb.disableAutoRange()
        if spec.manual is not None:
            self.vb.setYRange(*spec.manual, padding=0)

        # Cursor A follows the mouse (solid); B is the Δ anchor, dropped by a click (dashed).
        self.cursor = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(CURSOR, width=1))
        self.cursor.setVisible(False)  # until the mouse is over the plot
        self.anchor = pg.InfiniteLine(
            angle=90,
            movable=True,
            pen=pg.mkPen(CURSOR, style=QtCore.Qt.PenStyle.DashLine, width=1),
            hoverPen=pg.mkPen(CURSOR, style=QtCore.Qt.PenStyle.DashLine, width=2),
        )
        self.anchor.setVisible(False)
        for item in (self.cursor, self.anchor):
            self.plot.addItem(item, ignoreBounds=True)

        self.anchor.sigPositionChanged.connect(lambda line: owner.set_anchor(line.value()))
        self.vb.sigRangeChangedManually.connect(self._on_manual_range)
        self._build_menu()

    # --- graticule ---

    def _y_ticks(self) -> list[float]:
        """The left axis's major tick values in view: the graticule's horizontal lines."""
        lo, hi = self.vb.viewRange()[1]
        levels = self.plot.getAxis("left").tickValues(lo, hi, max(self.vb.height(), 1.0))
        return list(levels[0][1]) if levels else []

    def _on_range_changed(self, *_: object) -> None:
        self.graticule.update()
        self.markers.update()

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


class TelemetryPlot(QtWidgets.QWidget):
    cursor_moved = QtCore.pyqtSignal(str)
    readout_changed = QtCore.pyqtSignal(object)  # Readout | None
    trigger_level_changed = QtCore.pyqtSignal(float)  # the level line was dragged

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
        self._pending_cursor: float | None = None  # a live mouse move for the next frame
        self._last_frame_ts = -math.inf
        self._cursor_flush = QtCore.QTimer(self)
        self._cursor_flush.setSingleShot(True)
        self._cursor_flush.setInterval(CURSOR_FLUSH_MS)
        self._cursor_flush.timeout.connect(self._flush_cursor)
        # Exact values at a time, for the live readout (live frames hold min/max buckets):
        # (t, signal ids) -> {signal id: value}. Set by LiveFeed to the store's values_at.
        self.value_source: Callable[[float, list[str]], dict[str, float]] | None = None
        self._last_range_ts = 0.0
        self._ranges_due = True
        self._syncing_anchor = False
        # Overlaid traces of a previous capture (R4.5), dimmed and dashed.
        self._reference: list[tuple[Lane, pg.PlotDataItem]] = []
        self.last_readout: Readout | None = None
        # Overlays, re-created when the lanes are (R6.3, R6.5).
        self._markers: list[tuple[float, str]] = []
        self._marker_items: list[tuple[Lane, pg.InfiniteLine]] = []
        self._trigger: tuple[str, float] | None = None  # (signal, level)
        self._trigger_line: tuple[Lane, pg.InfiniteLine] | None = None
        self._capture_window: tuple[float, float] | None = None
        self._window_items: list[tuple[Lane, pg.LinearRegionItem]] = []

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
        self._marker_items = []
        self._trigger_line = None
        self._window_items = []
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
        self._draw_markers()
        self._draw_trigger_line()
        self._draw_capture_window()

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
        self._apply_mouse_mode()
        self._update_markers()
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
        self._last_frame_ts = time.perf_counter()
        # A mouse move since the last frame lands in this frame's paint (R10.1).
        self._flush_cursor()
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
            self._drop_pending_cursor()
            self.anchor_time = None
            self.anchor_values = {}
            for lane in self.lanes.values():
                lane.anchor.setVisible(False)
                lane.cursor.setVisible(False)
            self.clear_reference()
            self._cursor_x = None
            self.set_capture_window(None)
            self.last_readout = None
            self.readout_changed.emit(None)
        self._apply_mouse_mode()
        self._update_markers()
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
                x = float(vb.mapSceneToView(pos).x())
                live = self.mode == PlotMode.LIVE
                if live and time.perf_counter() - self._last_frame_ts < LIVE_FRAME_GAP_S:
                    self._pending_cursor = x
                    if not self._cursor_flush.isActive():
                        self._cursor_flush.start()
                else:
                    self.move_cursor(x)
                return

    def _flush_cursor(self) -> None:
        """Applies a deferred live mouse move (the next frame, or the timer if frames stop)."""
        x = self._pending_cursor
        self._drop_pending_cursor()
        if x is not None:
            self.move_cursor(x)

    def _drop_pending_cursor(self) -> None:
        self._pending_cursor = None
        self._cursor_flush.stop()

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
        self._update_markers()
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
        dt = x - self.anchor_time if self.anchor_time is not None else None
        deltas = (
            {sid: v - self.anchor_values.get(sid, math.nan) for sid, v in values.items()}
            if self.anchor_time is not None
            else {}
        )
        header = f"T = {x:.3f} s"
        if dt is not None:
            header += f"  (Δt {dt:+.3f} s)"
        self.last_readout = Readout(x, dt, values, deltas)
        self.cursor_moved.emit(header)
        self.readout_changed.emit(self.last_readout)

    def readout_text(self) -> str:
        """The readout of every shown lane as plain text (top to bottom), for tests and logs."""
        readout = self.last_readout
        if readout is None:
            return ""
        lines = [f"T = {readout.t:.3f} s"]
        if readout.dt is not None:
            lines[0] += f"  (Δt {readout.dt:+.3f} s)"
        for key in self._shown:
            for sid, view in self.signal_views.items():
                if view["lane"] != key or not view["visible"] or sid not in readout.values:
                    continue
                label = view["config"].get("label", sid)
                v = readout.values[sid]
                if not math.isfinite(v):
                    lines.append(f"{label}: n/a")
                    continue
                row = f"{label}: {v:+.3f}"
                d = readout.deltas.get(sid, math.nan)
                if math.isfinite(d):
                    row += f" (Δ {d:+.3f})"
                lines.append(row)
        return "\n".join(lines)

    # --- scope markers (R9.6) ---

    def _update_markers(self) -> None:
        """
        The `T` markers (the level in the trigger signal's lane, the trigger time at the
        top of the top lane) and, while stopped, the A/B flags on the top lane.
        """
        if not self.lanes or not self._shown:
            return
        stopped = self.mode == PlotMode.ANALYSIS
        a = self._cursor_x if stopped else None
        b = self.anchor_time if stopped else None
        trigger_lane = None
        if self._trigger is not None:
            view = self.signal_views.get(self._trigger[0])
            trigger_lane = view["lane"] if view is not None else None
        t_trig = self._capture_window[1] if self._capture_window is not None else None
        for key, lane in self.lanes.items():
            lane.markers.set_state(
                trigger_level=self._trigger[1]
                if self._trigger is not None and key == trigger_lane
                else None,
                trigger_time=t_trig if key == self._shown[0] else None,
                cursor_a=a,
                cursor_b=b,
                flags=key == self._shown[0],
            )

    def markers_of(self, lane_key: str) -> LaneMarkers:
        return self.lanes[lane_key].markers

    # --- command markers (R6.3) ---

    def set_markers(self, markers: list[tuple[float, str]]) -> None:
        """Vertical marks at the times commands were sent (stream time, seconds)."""
        self._markers = list(markers)
        self._draw_markers()

    def marker_count(self) -> int:
        return len(self._markers)

    def _draw_markers(self) -> None:
        for lane, item in self._marker_items:
            lane.plot.removeItem(item)
        self._marker_items = []
        pen = pg.mkPen(MARKER_COLOR, width=1, style=QtCore.Qt.PenStyle.DashLine)
        for t, label in self._markers:
            for lane in self.lanes.values():
                line = pg.InfiniteLine(pos=t, angle=90, movable=False, pen=pen)
                line.setToolTip(label)
                lane.plot.addItem(line, ignoreBounds=True)
                self._marker_items.append((lane, line))

    # --- trigger overlays (R6.5) ---

    def set_trigger_level(self, signal: str | None, level: float = 0.0) -> None:
        """
        Shows the trigger level as a draggable horizontal line in the lane of `signal`
        (None hides it). Dragging it emits `trigger_level_changed`.
        """
        self._trigger = (signal, float(level)) if signal is not None else None
        self._draw_trigger_line()
        self._update_markers()

    def trigger_line(self) -> pg.InfiniteLine | None:
        return self._trigger_line[1] if self._trigger_line is not None else None

    def _draw_trigger_line(self) -> None:
        if self._trigger_line is not None:
            lane, line = self._trigger_line
            lane.plot.removeItem(line)
            self._trigger_line = None
        if self._trigger is None:
            return
        signal, level = self._trigger
        view = self.signal_views.get(signal)
        if view is None:
            return
        lane = self.lanes[view["lane"]]
        line = pg.InfiniteLine(
            pos=level,
            angle=0,
            movable=True,
            pen=pg.mkPen(TRIGGER_COLOR, width=1, style=QtCore.Qt.PenStyle.DashLine),
            hoverPen=pg.mkPen(TRIGGER_COLOR, width=2),
        )
        line.setToolTip("Trigger level: drag to change")
        line.sigPositionChangeFinished.connect(
            lambda item: self.trigger_level_changed.emit(float(item.value()))
        )
        lane.plot.addItem(line, ignoreBounds=True)
        self._trigger_line = (lane, line)

    def set_capture_window(self, window: tuple[float, float] | None) -> None:
        """Shades part of a trigger capture, e.g. before the trigger (None clears it)."""
        self._capture_window = window
        self._draw_capture_window()
        self._update_markers()

    def capture_window(self) -> tuple[float, float] | None:
        return self._capture_window

    def _draw_capture_window(self) -> None:
        for lane, item in self._window_items:
            lane.plot.removeItem(item)
        self._window_items = []
        if self._capture_window is None:
            return
        brush = QtGui.QColor(TRIGGER_COLOR)
        brush.setAlpha(22)
        for lane in self.lanes.values():
            region = pg.LinearRegionItem(
                values=self._capture_window, movable=False, brush=brush, pen=pg.mkPen(None)
            )
            region.setZValue(-10)
            lane.plot.addItem(region, ignoreBounds=True)
            self._window_items.append((lane, region))
