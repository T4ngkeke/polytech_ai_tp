"""
test_history_trim.py — [v8.1] per-turn context cap (OOM protection).

The local inference engine can OOM on unbounded prompts; the 20-message
history cap bounds count but not size. `trim_history` bounds the ESTIMATED
token size of the chat history sent to the agent: oldest messages drop
first, the current question is never part of history, and a trimmed
history never starts with an orphan assistant reply. Estimation is
chars//3 — deliberately conservative (FR/EN run ~4 chars/token, so //3
overestimates the count), because this is a safety line, not billing.
"""

from backend.app.agent.history import estimate_tokens, trim_history


def _msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def test_estimate_tokens_is_chars_over_three():
    assert estimate_tokens("abcdef") == 2      # 6 chars → 2
    assert estimate_tokens("") == 0
    assert estimate_tokens("ab") == 1          # short text still counts ≥1


def test_history_under_budget_is_unchanged():
    history = [_msg("user", "salut"), _msg("assistant", "bonjour")]
    assert trim_history(history, max_tokens=1000) == history


def test_oldest_messages_drop_first():
    history = [
        _msg("user", "a" * 300),        # ~100 tokens — oldest, should drop
        _msg("assistant", "b" * 300),   # ~100 tokens — should drop
        _msg("user", "c" * 300),        # ~100 tokens — kept
        _msg("assistant", "d" * 300),   # ~100 tokens — kept
    ]
    trimmed = trim_history(history, max_tokens=200)
    assert trimmed == history[2:]


def test_trimmed_history_never_starts_with_assistant():
    history = [
        _msg("user", "a" * 300),
        _msg("assistant", "b" * 300),
        _msg("user", "c" * 300),
        _msg("assistant", "d" * 30),    # ~10 tokens
    ]
    # Budget fits the last assistant message only — an orphan reply with no
    # question is useless context, so it is dropped too.
    trimmed = trim_history(history, max_tokens=50)
    assert trimmed == []


def test_everything_over_budget_gives_empty_history():
    history = [_msg("user", "x" * 3000)]
    assert trim_history(history, max_tokens=100) == []


def test_zero_budget_gives_empty_history():
    history = [_msg("user", "salut")]
    assert trim_history(history, max_tokens=0) == []
