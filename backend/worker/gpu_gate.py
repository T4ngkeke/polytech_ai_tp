"""
gpu_gate.py — [v7] GPU-aware off-peak gate for the ingestion worker.

Decides whether the worker may pull ingestion work right now:
  * during off-peak windows (night / no class) → always allowed;
  * otherwise → only when smoothed GPU utilization is low, with hysteresis so
    the gate doesn't flap around a single threshold.

The GPU-utilization reader (`util_fn`) and off-peak check (`is_offpeak_fn`) are
injected, keeping this pure and testable. Production wires `util_fn` to pynvml.
"""

from collections import deque
from typing import Callable


class GpuGate:
    def __init__(
        self,
        *,
        util_fn: Callable[[], float],
        is_offpeak_fn: Callable[[], bool] = lambda: False,
        window: int = 5,
        start_threshold: float = 25.0,
        stop_threshold: float = 60.0,
    ):
        self._util_fn = util_fn
        self._is_offpeak_fn = is_offpeak_fn
        self._samples: deque[float] = deque(maxlen=window)
        self._start = start_threshold
        self._stop = stop_threshold
        self._open = False

    def should_run(self) -> bool:
        """Return whether the worker may pull ingestion work now."""
        if self._is_offpeak_fn():
            return True

        self._samples.append(self._util_fn())
        avg = sum(self._samples) / len(self._samples)

        # Hysteresis: open only below the low watermark; once open, stay open
        # until above the high watermark.
        if self._open:
            if avg > self._stop:
                self._open = False
        elif avg < self._start:
            self._open = True

        return self._open
