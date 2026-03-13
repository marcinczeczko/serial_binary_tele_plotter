import core
from core.types import EngineState, PlotMode


def test_core_init_imports():
    assert core is not None


def test_plot_mode_values():
    assert PlotMode.LIVE.value == 1
    assert PlotMode.ANALYSIS.value == 2


def test_engine_state_members():
    assert hasattr(EngineState, "IDLE")
    assert hasattr(EngineState, "CONFIGURED")
    assert hasattr(EngineState, "RUNNING")
