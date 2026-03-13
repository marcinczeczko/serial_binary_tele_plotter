import numpy as np

from core.acquisition.storage import SignalDataManager
from core.protocol.constants import LOOP_CNTR_NAME


def test_store_and_get_plot_data():
    mgr = SignalDataManager(max_samples=5)
    mgr.configure(
        {
            "sig_a": {"field": "a"},
            "sig_b": {"field": "b"},
        }
    )

    for i in range(4):
        mgr.store_frame({LOOP_CNTR_NAME: i, "a": i * 1.0, "b": i * 2.0})

    packet = mgr.get_plot_data(sample_period_s=0.1)
    assert packet is not None
    assert np.allclose(packet["time"], np.array([0.0, 0.1, 0.2, 0.3]))
    assert np.allclose(packet["signals"]["sig_a"], np.array([0, 1, 2, 3]))
    assert np.allclose(packet["signals"]["sig_b"], np.array([0, 2, 4, 6]))


def test_update_max_samples_keeps_recent():
    mgr = SignalDataManager(max_samples=5)
    mgr.configure({"sig_a": {"field": "a"}})
    for i in range(5):
        mgr.store_frame({LOOP_CNTR_NAME: i, "a": i})

    mgr.update_max_samples(3)
    packet = mgr.get_plot_data(sample_period_s=1.0)
    assert packet is not None
    assert np.allclose(packet["signals"]["sig_a"], np.array([2, 3, 4]))
