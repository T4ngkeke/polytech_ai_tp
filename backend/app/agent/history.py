"""
history.py — how much of the chat history goes with a question.

The local inference engine is the one hard dependency that can fall over: an
unbounded prompt grows the KV cache until the box OOMs. This module bounds the
estimated token size of the history.

Estimation is deliberately cheap and conservative: ``chars // 3``. FR/EN text
runs ~4 chars per token, so //3 *overestimates* the count — safe for a limit.
No tokenizer dependency (red line: no torch in the image); billing still uses
the engine's real usage numbers, never this estimate.

The limit is a share of the model's context (HISTORY_CONTEXT_PERCENT, 30% by
default: about 39k tokens with the 128k server; 2026-11-18, it was 7k tokens).
Past the limit the oldest turns go, several at once (``history_start``), so the
start of the prompt stays the same for the next questions and the model server
can reuse its cache. The chat page draws a line where the history starts.
What drops out is summarized (agent/compaction.py) unless that is turned off.
"""

# Default context when the model server does not say (services/model_servers.context_tokens).
DEFAULT_CONTEXT = 65536
# A cut drops about this share of the limit at least, so the next cut comes
# only after as much new conversation.
STEP = 0.3


def estimate_length(n: int | None) -> int:
    """The estimate of a text of ``n`` characters: ``n // 3``, minimum 1 when
    not empty."""
    return max(1, n // 3) if n else 0


def estimate_tokens(text: str) -> int:
    """Conservative token estimate: ``len // 3``, minimum 1 for non-empty text."""
    return estimate_length(len(text or ''))


def history_budget(context_tokens: int | None, percent: int) -> int:
    """Estimated tokens of history a question may carry: ``percent`` of the context."""
    return (context_tokens or DEFAULT_CONTEXT) * max(0, percent) // 100


def history_start(history: list[dict], *, max_tokens: int, offset: int = 0) -> int:
    """Index of the first message of ``history`` that is sent.

    Everything is sent while it fits. Past the limit, the history may start
    only at fixed points of the conversation: one every STEP × max_tokens of
    text, counted from its first message (``offset``: the estimated tokens of
    the session's messages before ``history``). The earliest point after which the
    rest fits is used. So the start moves in jumps and stays put for the next
    questions, unlike a start recomputed from the newest message each time.
    When no point fits (one huge message), the newest messages that fit are
    kept. The history never starts with an assistant message: a reply whose
    question was dropped is useless context.
    """
    if max_tokens <= 0:
        return len(history)
    costs = [estimate_tokens(m.get("content") or "") for m in history]
    total = sum(costs)
    start = 0 if total <= max_tokens else None
    if start is None:
        step = max(1, int(max_tokens * STEP))
        sent, mark = offset, (offset // step + 1) * step
        for i, cost in enumerate(costs):
            if sent >= mark:
                while sent >= mark:
                    mark += step
                if total - (sent - offset) <= max_tokens:
                    start = i
                    break
            sent += cost
    if start is None:
        # The newest suffix that fits.
        start, kept = len(history), 0
        for i in range(len(history) - 1, -1, -1):
            if kept + costs[i] > max_tokens:
                break
            kept += costs[i]
            start = i
    while start < len(history) and history[start].get("role") == "assistant":
        start += 1
    return start

