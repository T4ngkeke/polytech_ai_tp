"""
test_gpu_gate.py — v7 GPU-aware off-peak gate for the ingestion worker.

Pure logic: the GPU-utilization reader and the off-peak check are injected, so
no real GPU (pynvml) or wall clock is needed.
"""

from backend.worker.gpu_gate import GpuGate


def _util_from(values):
    """A util_fn that yields successive values from a list."""
    it = iter(values)
    return lambda: next(it)


def test_gate_open_during_offpeak_regardless_of_load():
    gate = GpuGate(util_fn=lambda: 99.0, is_offpeak_fn=lambda: True)
    assert gate.should_run() is True


def test_gate_closed_when_utilization_high():
    gate = GpuGate(util_fn=lambda: 80.0, window=1)
    assert gate.should_run() is False


def test_closed_gate_stays_closed_in_hysteresis_band():
    # Between start (25) and stop (60): not low enough to open.
    gate = GpuGate(util_fn=lambda: 40.0, window=1)
    assert gate.should_run() is False


def test_open_gate_stays_open_until_high_watermark():
    gate = GpuGate(util_fn=_util_from([10.0, 40.0, 70.0]), window=1)
    assert gate.should_run() is True   # 10 < 25 → opens
    assert gate.should_run() is True   # 40 in band → stays open (sticky)
    assert gate.should_run() is False  # 70 > 60 → closes
