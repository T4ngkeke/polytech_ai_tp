"""
gpu_gate.py — [v7.1] off-peak gating for the ingestion worker.

Two gates decide whether the worker may pull ingestion work right now:

  * `ChatLoadGate` (v7.1, primary, engine-agnostic): pause ingestion while
    students are actively chatting. The signal is recent chat activity (a count
    sampled from the DB by the caller and passed to `observe`), so it works the
    same whether the engine is a local GPU or a remote API.
  * `GpuGate` (optional, local only): also require the GPU idle. On a remote API
    there is no GPU, so its `util_fn` reads idle (0.0) and this gate no-ops.

Both apply hysteresis (low/high watermarks) so they don't flap around a single
threshold. All inputs are injected, keeping the logic pure and testable: the
worker wires `util_fn` to pynvml and feeds `ChatLoadGate.observe` a DB count.
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


class ChatLoadGate:
    """[v7.1] Primary, engine-agnostic gate: pause ingestion while chat is busy.

    The caller samples recent chat activity (e.g. a count of recent `Messages`
    from the DB) and passes it to `observe`. The gate is OPEN (ingestion allowed)
    while chat is quiet and CLOSED while students are active, with hysteresis so
    it doesn't flap. Pure and synchronous — the async DB read lives in the caller.
    """

    def __init__(
        self,
        *,
        window: int = 5,
        start_threshold: float = 2.0,
        stop_threshold: float = 6.0,
    ):
        self._samples: deque[float] = deque(maxlen=window)
        self._start = start_threshold  # open (allow ingest) when load < start
        self._stop = stop_threshold    # close (pause) when load > stop
        self._open = False

    def observe(self, load: float) -> bool:
        """Record the latest chat-load sample and return whether ingestion may run."""
        self._samples.append(load)
        avg = sum(self._samples) / len(self._samples)

        # Hysteresis: open only when load is clearly low; once open, stay open
        # until load climbs above the high watermark.
        if self._open:
            if avg > self._stop:
                self._open = False
        elif avg < self._start:
            self._open = True

        return self._open
