"""Signal rows (R9.4): left/right pairs for the Signals pane. Qt-free."""

from __future__ import annotations

from typing import Any

from ui.panels.signal_rows import SignalRow, lane_title, pair_rows, side_of


def test_pairs_by_label_prefix_in_first_appearance_order() -> None:
    signals: dict[str, Any] = {
        "a": {"label": "L: Speed"},
        "b": {"label": "Temp"},
        "c": {"label": "R: Speed"},
        "d": {"label": "R: Error"},
        "e": {"label": "L: Error"},
    }
    assert pair_rows(list(signals), signals) == [
        SignalRow("Speed", left="a", right="c"),
        SignalRow("Temp", left="b"),
        SignalRow("Error", left="e", right="d"),  # R first: still one row, in R's place
    ]


def test_pairs_by_key_prefix_without_a_labelled_side() -> None:
    signals: dict[str, Any] = {
        "left_pwm": {"label": "PWM left"},
        "right_pwm": {"label": "PWM right"},
        "left_only": {"label": "Only"},
    }
    rows = pair_rows(list(signals), signals)
    assert rows[0] == SignalRow("PWM left", left="left_pwm", right="right_pwm")
    assert rows[1] == SignalRow("Only", left="left_only")  # no partner: one swatch


def test_a_side_pairs_once_and_labels_decide_before_keys() -> None:
    signals: dict[str, Any] = {
        "x": {"label": "L: A"},
        "y": {"label": "R: A"},
        "z": {"label": "R: A"},  # a second right copy stands alone
        "right_q": {"label": "Q"},
    }
    rows = pair_rows(list(signals), signals)
    assert [r.members for r in rows] == [["x", "y"], ["z"], ["right_q"]]
    assert rows[2].right == "right_q" and rows[2].left is None
    assert side_of("left_x", {"label": "R: X"})[0] == "right"


def test_the_bundled_pid_stream_is_17_rows() -> None:
    from core.config import DEFAULT_CONFIG_PATH, StreamConfigLoader
    from ui.charts.lanes import lane_layout

    cfg = StreamConfigLoader(DEFAULT_CONFIG_PATH).get_stream("pid")
    specs, assignment = lane_layout(cfg)
    signals = cfg["signals"]
    rows = [
        row
        for spec in specs
        for row in pair_rows([s for s in signals if assignment[s] == spec.key], signals)
    ]
    assert len(signals) == 34 and len(rows) == 17
    assert all(r.left and r.right for r in rows)


def test_lane_title() -> None:
    assert lane_title("Speed [rps]") == "SPEED rps"
    assert lane_title("Control [%]") == "CONTROL %"
    assert lane_title("Error / integral") == "ERROR / INTEGRAL"
