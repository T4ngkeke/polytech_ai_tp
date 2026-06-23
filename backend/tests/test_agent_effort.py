"""
test_agent_effort.py — v7 per-message effort/clarity score (0..1).

Rewards thinking over copy-paste: an attempt, a specific error, an exercise
reference, and real prose. Feeds the smoothed sliding window.
"""

from backend.app.agent.effort import assess_effort


def test_low_effort_dump_scores_low():
    msg = "debug\n```\nfor i in range(10):\n    print(x[i])\n```"
    assert assess_effort(msg) < 0.3


def test_structured_question_scores_high():
    msg = (
        "In chapter 1, exercise 2, I tried a for-loop but got an IndexError on "
        "line 3; I think the index is off by one — why does that happen?"
    )
    assert assess_effort(msg) > 0.7


def test_score_is_bounded():
    assert 0.0 <= assess_effort("") <= 1.0
