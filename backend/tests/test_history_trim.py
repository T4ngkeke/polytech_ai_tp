"""
test_history_trim.py — per-turn context cap (OOM protection).

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


# --- The limit as a share of the context, and a start that moves in jumps ---

from backend.app.agent.history import MAX_SHARED_PERCENT, history_budget, history_start  # noqa: E402


def _turns(n: int, chars: int = 300) -> list[dict]:
    out = []
    for i in range(n):
        out += [_msg("user", f"q{i} " + "x" * (chars - 4)), _msg("assistant", f"a{i} " + "y" * (chars - 4))]
    return out


def test_budget_is_a_share_of_the_context():
    assert history_budget(131072, 30) == 39321
    assert history_budget(None, 30) == 65536 * 30 // 100     # server did not say
    assert history_budget(131072, 0) == 0


def test_budget_leaves_room_next_to_the_tp_text():
    # TP 30% + history 30% fit under MAX_SHARED_PERCENT; TP 60% leaves 15%.
    assert history_budget(100000, 30, tp_percent=30) == 30000
    assert history_budget(100000, 30, tp_percent=60) == (MAX_SHARED_PERCENT - 60) * 1000
    assert history_budget(100000, 30, tp_percent=80) == 0


def test_start_stays_put_for_several_questions():
    """Past the limit the start jumps forward, then stays while new turns
    come: the prompt keeps the same beginning (prefix cache)."""
    budget = 2000                       # tokens; one turn is 200
    starts = [history_start(_turns(n), max_tokens=budget) for n in range(1, 30)]
    assert starts[:10] == [0] * 10      # 10 turns = 2000 tokens: all sent
    assert all(b >= a for a, b in zip(starts, starts[1:]))  # never goes back
    moves = sum(b != a for a, b in zip(starts, starts[1:]))
    assert moves <= len(starts) // 3    # not at every question
    for n, start in zip(range(1, 30), starts):
        history = _turns(n)
        kept = sum(estimate_tokens(m["content"]) for m in history[start:])
        assert kept <= budget
        assert start % 2 == 0           # starts with a question


def test_one_huge_message_keeps_the_newest_that_fit():
    history = [_msg("user", "a" * 300), _msg("assistant", "b" * 300),
               _msg("user", "c" * 30000), _msg("assistant", "d" * 300),
               _msg("user", "e" * 300), _msg("assistant", "f" * 300)]
    assert history_start(history, max_tokens=500) == 4


def test_older_messages_not_loaded_keep_the_cut_points_in_place():
    """A long session is read from its last messages only; their offset (the
    size of the older ones) keeps the start where the whole history puts it."""
    budget = 2000
    whole = _turns(40)
    for dropped in (0, 10, 20, 30):          # oldest messages not loaded
        window = whole[dropped:]
        offset = sum(estimate_tokens(m["content"]) for m in whole[:dropped])
        start = history_start(window, max_tokens=budget, offset=offset)
        assert dropped + start == history_start(whole, max_tokens=budget)


def test_research_input_ceiling_stays_under_the_context():
    from backend.app.services.research_service import CONTEXT_RESERVE, research_input_tokens
    history = [_msg("user", "x" * 30000)]      # 10,000 estimated tokens
    assert research_input_tokens(28000, history, 131072) == 38000
    assert research_input_tokens(200000, history, 131072) == 131072 - CONTEXT_RESERVE
    assert research_input_tokens(28000, None, None) == 28000
