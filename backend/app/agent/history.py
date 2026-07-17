"""
history.py — [v8.1] per-turn context cap on chat history (OOM protection).

The local inference engine is the one hard dependency that can fall over: an
unbounded prompt grows the KV cache until the box OOMs. The 20-message history
cap bounds *count*, not *size* — this module bounds the estimated token size.

Estimation is deliberately cheap and conservative: ``chars // 3``. FR/EN text
runs ~4 chars per token, so //3 *overestimates* the count — safe for a limit.
No tokenizer dependency (red line: no torch in the image); billing still uses
the engine's real usage numbers, never this estimate.
"""


def estimate_tokens(text: str) -> int:
    """Conservative token estimate: ``len // 3``, minimum 1 for non-empty text."""
    if not text:
        return 0
    return max(1, len(text) // 3)


def trim_history(history: list[dict], *, max_tokens: int) -> list[dict]:
    """Keep the newest suffix of ``history`` whose estimated tokens fit the budget.

    Oldest messages drop first (the current question is not part of history and
    is never trimmed). A surviving suffix never *starts* with an assistant
    message — an orphan reply whose question was trimmed is useless context, so
    leading assistant messages are dropped too.
    """
    if max_tokens <= 0:
        return []

    total = 0
    start = len(history)  # index of the first kept message
    for i in range(len(history) - 1, -1, -1):
        cost = estimate_tokens(history[i].get("content") or "")
        if total + cost > max_tokens:
            break
        total += cost
        start = i

    while start < len(history) and history[start].get("role") == "assistant":
        start += 1
    return history[start:]
