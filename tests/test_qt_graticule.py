"""
The plot's scope look (R9.6): framed graticule lanes, the time unit on the last X tick,
trigger `T` markers and cursor `A`/`B` flags, all without a second paint per frame.

Marked `qt`: skipped when pytest-qt is disabled or Qt can't load (see tests/conftest.py).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6 import QtCore, QtGui  # noqa: E402

pytestmark = pytest.mark.qt

SIGNALS: dict[str, Any] = {
    "speed": {"label": "Speed", "field": "speed", "color": "#FFE81A", "group": "slow"},
    "pwm": {"label": "PWM", "field": "pwm", "color": "#FF5A5A", "group": "big"},
}
GROUPS: dict[str, Any] = {
    "slow": {"label": "Speed [rps]", "order": 1},
    "big": {"label": "PWM", "order": 2},
}


def _plot(qtbot: Any) -> Any:
    from ui.charts.telemetry_plot import TelemetryPlot

    plot = TelemetryPlot()
    qtbot.addWidget(plot)
    plot.resize(900, 500)
    plot.show()
    qtbot.waitExposed(plot)
    plot.configure_signals(SIGNALS, GROUPS)
    return plot


def _packet(n: int = 3000) -> dict[str, Any]:
    t = np.arange(n) * 0.001
    signals = {"speed": 0.3 * np.sin(t * 3), "pwm": 100.0 + 50 * np.sin(t)}
    bounds = {k: (float(v.min()), float(v.max())) for k, v in signals.items()}
    return {"time": t, "signals": signals, "signal_bounds": bounds}


def test_lanes_are_graticules_without_the_axis_grid(qtbot: Any) -> None:
    from ui.charts.graticule import Graticule, LaneMarkers

    plot = _plot(qtbot)
    plot.show_packet(_packet())
    for lane in plot.lanes.values():
        assert isinstance(lane.graticule, Graticule) and isinstance(lane.markers, LaneMarkers)
        assert lane.graticule.parentItem() is lane.vb  # pixel coordinates, not data
        assert lane.graticule.zValue() < lane.vb.childGroup.zValue() < lane.markers.zValue()
        assert lane.graticule.boundingRect() == QtCore.QRectF(lane.vb.rect())
        assert not lane.plot.getAxis("left").grid and not lane.plot.getAxis("bottom").grid
    ticks = plot.lanes["slow"]._y_ticks()
    assert ticks and 0.0 in ticks  # the zero line is one of the horizontal lines
    assert plot.lanes["slow"].plot.getAxis("left").labelText == "SPEED rps"
    assert plot.lanes["big"].plot.getAxis("bottom").labelText == ""  # no "Time [s]" row


def test_the_time_unit_goes_on_the_last_label_shown(qtbot: Any) -> None:
    plot = _plot(qtbot)
    plot.show_packet(_packet())
    qtbot.wait(50)
    axis = plot.lanes["big"].plot.getAxis("bottom")
    values = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    strings = axis.tickStrings(values, 1.0, 0.5)
    shown = [s for s in strings if s]
    assert shown[-1].endswith(" s") and sum(s.endswith(" s") for s in strings) == 1
    assert all(s == "" for s in strings[strings.index(shown[-1]) + 1 :])


def test_trigger_and_cursor_markers(qtbot: Any) -> None:
    plot = _plot(qtbot)
    plot.show_packet(_packet())
    plot.set_trigger_level("speed", 0.15)
    speed, pwm = plot.markers_of("slow"), plot.markers_of("big")
    assert speed.trigger_level == 0.15 and pwm.trigger_level is None  # its lane only
    assert speed.flags and not pwm.flags  # the top lane carries the A/B flags

    plot.set_paused(True)
    plot.set_capture_window((1.5, 2.0))  # a capture: T at the trigger time, top lane
    plot.set_anchor(2.0)
    plot.move_cursor(2.4)
    assert speed.trigger_time == 2.0 and pwm.trigger_time is None
    assert (speed.cursor_a, speed.cursor_b) == (pytest.approx(2.4), 2.0)

    plot.set_paused(False)  # live: no flags, no capture
    assert speed.cursor_a is None and speed.cursor_b is None and speed.trigger_time is None


def test_markers_paint_without_moving(qtbot: Any) -> None:
    """Painting the overlays must not change their geometry (a second paint, R3.4)."""
    plot = _plot(qtbot)
    plot.show_packet(_packet())
    plot.set_trigger_level("speed", 0.15)
    plot.set_paused(True)
    plot.set_anchor(1.0)
    plot.move_cursor(2.0)
    lane = plot.lanes["slow"]
    before = (lane.markers.boundingRect(), lane.graticule.boundingRect())
    image = QtGui.QImage(900, 500, QtGui.QImage.Format.Format_ARGB32)
    painter = QtGui.QPainter(image)
    lane.graticule.paint(painter)
    lane.markers.paint(painter)
    painter.end()
    assert (lane.markers.boundingRect(), lane.graticule.boundingRect()) == before
