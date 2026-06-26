"""
test_gpu_gate.py — v7 GPU-aware off-peak gate for the ingestion worker.

Pure logic: the GPU-utilization reader and the off-peak check are injected, so
no real GPU (pynvml) or wall clock is needed.
"""

from backend.worker.gpu_gate import ChatLoadGate, GpuGate


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


# ── ChatLoadGate (v7.1 primary, engine-agnostic) ──
# Defaults: open (allow ingest) when load < 2, close (pause) when load > 6.

def test_chat_gate_open_when_chat_quiet():
    gate = ChatLoadGate(window=1)
    assert gate.observe(0) is True   # no recent chat → ingest allowed


def test_chat_gate_closed_when_chat_busy():
    gate = ChatLoadGate(window=1)
    assert gate.observe(10) is False  # lots of recent chat → pause ingestion


def test_chat_gate_stays_closed_in_hysteresis_band():
    gate = ChatLoadGate(window=1)
    assert gate.observe(4) is False   # between start (2) and stop (6): not quiet enough


def test_chat_gate_open_stays_open_until_high_watermark():
    gate = ChatLoadGate(window=1)
    assert gate.observe(0) is True    # 0 < 2 → opens
    assert gate.observe(4) is True    # 4 in band → stays open (sticky)
    assert gate.observe(8) is False   # 8 > 6 → closes
