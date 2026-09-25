"""
Plot lanes, range modes and cursor readout against real Qt (R3.1-R3.3).

Marked `qt`: skipped when pytest-qt is disabled or Qt can't load (see tests/conftest.py).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

pytestmark = pytest.mark.qt

SIGNALS: dict[str, Any] = {
    "speed": {"label": "Speed", "field": "speed", "color": "#4FC3F7", "group": "slow"},
    "pwm": {"label": "PWM", "field": "pwm", "color": "#FFB74D", "group": "big"},
    "err": {"label": "Err", "field": "err", "color": "#E57373", "group": "slow"},
    "hidden": {
        "label": "Hidden",
        "field": "h",
        "color": "#fff",
        "group": "extra",
        "visible": False,
    },
}
GROUPS: dict[str, Any] = {
    "slow": {"label": "Speed [rps]", "order": 1},
    "big": {"label": "PWM", "order": 2},
    "extra": {"label": "Extra", "order": 3},
}


def _plot(qtbot: Any) -> Any:
    from ui.charts.telemetry_plot import TelemetryPlot

    plot = TelemetryPlot()
    qtbot.addWidget(plot)
    plot.resize(900, 600)
    plot.show()
    plot.configure_signals(SIGNALS, GROUPS)
    return plot


def _packet(n: int = 1000, start: int = 0) -> dict[str, Any]:
    t = (np.arange(n) + start) * 0.001
    signals = {
        "speed": 0.3 * np.sin(t),
        "pwm": 1000.0 + 50 * np.sin(t),
        "err": 0.01 * np.cos(t),
        "hidden": np.full(n, 7.0),
    }
    bounds = {k: (float(np.nanmin(v)), float(np.nanmax(v))) for k, v in signals.items()}
    return {"time": t, "signals": signals, "signal_bounds": bounds}


def _y_range(plot: Any, lane: str) -> tuple[float, float]:
    lo, hi = plot.lanes[lane].vb.viewRange()[1]
    return float(lo), float(hi)


def test_signals_are_drawn_in_their_groups_lanes(qtbot: Any) -> None:
    plot = _plot(qtbot)

    assert plot.shown_lanes() == ["slow", "big"]  # "extra" holds only a hidden signal
    assert plot.lanes["slow"].plot.getAxis("left").labelText == "SPEED rps"  # small caps (R9.6)
    plot.set_signal_visible("hidden", True)
    assert plot.shown_lanes() == ["slow", "big", "extra"]
    plot.set_signal_visible("pwm", False)
    assert plot.shown_lanes() == ["slow", "extra"]
    # The lanes share one time axis
    assert plot.lanes["extra"].vb.linkedView(0) is plot.lanes["slow"].vb


def test_each_lane_fits_its_own_signals(qtbot: Any) -> None:
    # A4: PWM in the hundreds and speed around 0.3 no longer share one axis.
    plot = _plot(qtbot)
    plot.show_packet(_packet())

    slow, big = _y_range(plot, "slow"), _y_range(plot, "big")
    assert -0.05 < slow[0] < -0.01 < 0.25 < slow[1] < 0.3  # 0.3 sin(t), t in [0, 1)
    assert 900 < big[0] < 1000 < big[1] < 1100  # zero is not forced in
    x0, x1 = plot.lanes["big"].vb.viewRange()[0]
    assert x0 == pytest.approx(0.0) and x1 == pytest.approx(0.999)  # X follows the data


def test_user_y_zoom_holds_and_the_menu_resumes_auto(qtbot: Any) -> None:
    plot = _plot(qtbot)
    plot.show_packet(_packet())
    lane = plot.lanes["big"]

    lane.vb.setYRange(0.0, 5000.0, padding=0)
    lane.vb.sigRangeChangedManually.emit([False, True])  # what a wheel/drag on Y emits
    plot.refresh_ranges()
    plot.show_packet(_packet(start=1000))
    assert lane.mode == "manual"
    assert _y_range(plot, "big") == pytest.approx((0.0, 5000.0))  # not reset by live frames
    assert lane._mode_actions["manual"].isChecked()

    lane._mode_actions["auto"].trigger()
    plot.show_packet(_packet(start=2000))
    assert lane.mode == "auto" and _y_range(plot, "big")[1] < 1100


def test_auto_grow_only_widens_and_include_zero(qtbot: Any) -> None:
    plot = _plot(qtbot)
    plot.set_lane_mode("big", "auto-grow")
    plot.show_packet(_packet())
    first = _y_range(plot, "big")
    quiet = _packet(start=1000)
    quiet["signals"]["pwm"] = np.full(1000, 1000.0)
    quiet["signal_bounds"]["pwm"] = (1000.0, 1000.0)
    plot.show_packet(quiet)
    assert _y_range(plot, "big") == pytest.approx(first)  # did not shrink

    plot.lanes["big"].set_include_zero(True)
    plot.show_packet(quiet)
    assert _y_range(plot, "big")[0] < 0


def test_live_frames_are_decimated_but_the_readout_is_exact(qtbot: Any) -> None:
    plot = _plot(qtbot)
    packet = _packet(n=100_000)
    plot.show_packet(packet)

    curve = plot.signal_views["pwm"]["curve"]
    assert len(curve.xData) <= 3 * max(int(plot.lanes["slow"].vb.width()), 300)
    assert np.nanmax(curve.yData) == pytest.approx(packet["signal_bounds"]["pwm"][1])

    plot.move_cursor(12.3456)
    text = plot.readout_text()
    expected = 1000.0 + 50 * np.sin(12.3456)
    assert "T = 12.346 s" in text
    assert f"PWM: {expected:+.3f}" in text
    assert "Hidden" not in text


def test_paused_readout_has_delta_anchor_and_reads_gaps_as_na(qtbot: Any) -> None:
    plot = _plot(qtbot)
    packet = _packet(n=100)
    packet["signals"]["err"][50] = np.nan  # a gap marker
    plot.set_paused(True, packet)

    plot.set_anchor(0.010)
    plot.move_cursor(0.030)
    text = plot.readout_text()
    assert "(Δt +0.020 s)" in text
    d_speed = 0.3 * (np.sin(0.030) - np.sin(0.010))
    assert f"(Δ {d_speed:+.3f})" in text
    assert all(plot.lanes[k].anchor.isVisible() for k in plot.shown_lanes())
    plot.lanes["big"].anchor.setValue(0.020)  # dragging one anchor moves them all
    assert plot.anchor_time == pytest.approx(0.020)
    assert plot.lanes["slow"].anchor.value() == pytest.approx(0.020)

    plot.move_cursor(0.0505)
    assert "Err: n/a" in plot.readout_text()

    plot.set_paused(False)
    assert plot.anchor_time is None
    assert not any(lane.anchor.isVisible() for lane in plot.lanes.values())


def test_paused_auto_lanes_fit_only_the_visible_time_window(qtbot: Any) -> None:
    plot = _plot(qtbot)
    packet = _packet(n=1000)
    packet["signals"]["pwm"][:500] = 5000.0  # a big excursion early on
    plot.set_paused(True, packet)
    assert _y_range(plot, "big")[1] > 5000

    plot.plot.setXRange(0.6, 0.9, padding=0)  # zoom to after the excursion

    assert _y_range(plot, "big")[1] < 1100


def test_moving_a_signal_to_a_new_lane(qtbot: Any) -> None:
    plot = _plot(qtbot)
    plot.move_signal("err", "Lane 4", "Lane 4")

    assert plot.shown_lanes() == ["slow", "big", "Lane 4"]
    assert plot.signal_views["err"]["lane"] == "Lane 4"
    assert plot.signal_views["err"]["curve"] in plot.lanes["Lane 4"].plot.listDataItems()
    plot.show_packet(_packet())
    assert _y_range(plot, "Lane 4")[1] < 0.02  # fits the small error signal alone


def test_signal_panel_groups_by_lane_and_moves_signals(qtbot: Any) -> None:
    from ui.charts.series import Readout
    from ui.panels.signals import NEW_LANE, SignalListPanel

    panel = SignalListPanel()
    qtbot.addWidget(panel)
    panel.rebuild_list({"signals": SIGNALS, "groups": GROUPS})
    moves: list[tuple[str, str, str]] = []
    shown: list[tuple[str, bool]] = []
    panel.signal_lane_changed.connect(lambda *a: moves.append(a))
    panel.signal_visibility_changed.connect(lambda *a: shown.append(a))

    assert [label for _, label in panel.lanes()] == ["Speed [rps]", "PWM", "Extra"]
    panel.move_to_lane("err", "big")
    panel.move_to_lane("err", NEW_LANE)
    assert moves == [("err", "big", "PWM"), ("err", "Lane 4", "Lane 4")]
    assert panel.lane_of("err") == "Lane 4" and ("Lane 4", "Lane 4") in panel.lanes()

    panel.set_lane_visible("Lane 4", False)  # a lane's box hides all its signals
    assert shown == [("err", False)] and not panel.is_visible("err")
    panel.filter_edit.setText("spe")
    assert panel.tree.topLevelItemCount() == 4  # lanes stay, non-matching ones hidden
    items = [panel.tree.topLevelItem(i) for i in range(4)]
    hidden = [item.isHidden() for item in items if item is not None]
    assert hidden.count(False) == 1

    panel.show_readout(Readout(1.5, 0.25, {"speed": 0.125, "err": 1.0}, {"speed": 0.5}))
    assert panel.cursor_lbl.text() == "A 1.5s  B 1.25s  ΔT -0.25s"  # B is the anchor
    assert panel.value_text("speed") == "0.125"  # its value at A; Δ is in the tooltip
    item = panel._items["speed"]
    assert "Δ (A − B) +0.5" in item.toolTip(1)
    assert panel.value_text("err") == ""  # hidden signals show no value


def test_stream_editor_lanes_round_trip_and_change(qtbot: Any) -> None:
    import copy

    from ui.config.stream_editor import StreamEditor

    editor = StreamEditor()
    qtbot.addWidget(editor)
    stream: dict[str, Any] = {
        "name": "S",
        "frame": {"stream_id": 1, "fields": [{"name": "loop_cntr", "type": "u32"}]},
        "groups": GROUPS,
        "signals": {
            "a": {"label": "A", "field": "loop_cntr", "group": "big", "color": "#fff"},
            "b": {"label": "B", "field": "loop_cntr", "color": "#fff"},
        },
    }
    editor.load_data("s", copy.deepcopy(stream))
    _, data = editor.get_data()
    assert data == stream

    editor.select("loop_cntr", "a")
    combo = editor.lane_combo
    combo.setCurrentIndex(combo.findData(""))  # the default lane
    combo.activated.emit(combo.currentIndex())
    editor.select("loop_cntr", "b")
    combo.setCurrentText("fresh")  # a new lane, by name
    line_edit = combo.lineEdit()
    assert line_edit is not None
    line_edit.editingFinished.emit()

    _, data = editor.get_data()
    assert "group" not in data["signals"]["a"]
    assert data["signals"]["b"]["group"] == "fresh"
    assert data["groups"]["fresh"]["label"] == "fresh"


def test_live_feed_draws_the_overview_and_reads_exact_values(qtbot: Any) -> None:
    import math

    from core.acquisition.storage import SampleStore
    from core.acquisition.timebase import TimeBaseConfig
    from ui.charts.live_feed import LiveFeed

    plot = _plot(qtbot)
    store = SampleStore(50_000)
    store.configure(SIGNALS, TimeBaseConfig(scale_s=0.001))
    store.append(
        {"loop_cntr": i, "speed": math.sin(i / 700), "pwm": 1000.0 + i % 13, "err": 0.0}
        for i in range(50_000)
    )
    feed = LiveFeed(store, plot)
    feed.timer.stop()

    feed.tick()

    curve = plot.signal_views["speed"]["curve"]
    assert len(curve.xData) < 3_500  # min/max buckets, not 50k samples
    assert np.nanmax(curve.yData) == pytest.approx(1.0, abs=1e-6)  # extremes kept
    plot.move_cursor(12.3456)  # between samples 12345 and 12346
    expected = math.sin(12345.6 / 700)
    assert f"Speed: {expected:+.3f}" in plot.readout_text()
    feed.set_store(None)
    assert plot.value_source is None
