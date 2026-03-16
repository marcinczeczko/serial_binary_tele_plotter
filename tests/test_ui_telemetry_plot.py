import numpy as np


def test_compute_y_bounds_ignores_hidden(pyqt_stub):
    from ui.charts.telemetry_plot import TelemetryPlot

    class _Curve:
        def __init__(self, visible: bool):
            self._visible = visible

        def isVisible(self) -> bool:
            return self._visible

    plot = TelemetryPlot.__new__(TelemetryPlot)  # bypass __init__; safe in Python 3.14+
    plot.signal_views = {
        "a": {"curve": _Curve(True)},
        "b": {"curve": _Curve(False)},
    }

    signals = {
        "a": np.array([1.0, 2.0, 3.0]),
        "b": np.array([-100.0, 100.0]),
    }

    # Pass empty signal_bounds so the fallback nanmin/nanmax path is exercised
    lo, hi = plot._compute_y_bounds(signals, {})
    assert lo == 1.0
    assert hi == 3.0


def test_update_tooltip_anchor_zero(pyqt_stub):
    from ui.charts.telemetry_plot import TelemetryPlot

    class _Curve:
        def isVisible(self) -> bool:
            return True

    class _Label:
        def __init__(self):
            self.html = ""

        def setHtml(self, html: str) -> None:
            self.html = html

    plot = TelemetryPlot.__new__(TelemetryPlot)  # bypass __init__; safe in Python 3.14+
    plot.label = _Label()
    plot.update_hud_position = lambda: None
    plot.anchor_time = 0.0
    plot.anchor_values = {"sig": 1.0}
    plot.signal_views = {
        "sig": {"curve": _Curve(), "config": {"color": "#fff", "label": "Sig"}}
    }

    ds = {
        "time": np.array([0.0, 1.0]),
        "signals": {"sig": np.array([1.0, 2.0])},
    }

    plot.update_tooltip(0.0, ds)
    assert "(Δ +0.000 s)" in plot.label.html
    assert "(Δ +0.000)" in plot.label.html
