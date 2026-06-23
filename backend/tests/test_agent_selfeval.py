"""
test_agent_selfeval.py — [v7.1] bounded self-eval retrieval loop.

Pure orchestration over injected retrieve / grade / rewrite functions:
  * cheap gate first — empty recall is judged `bad` WITHOUT calling the grader,
  * a 3-tier verdict (good/partial/bad); good|partial are used as-is,
  * `bad` rewrites the query and re-retrieves, bounded by max_retries,
  * exhausting the budget while still bad raises the disclaimer flag.
"""

import pytest

from backend.app.agent.selfeval import run_self_eval_loop


class _Hit:
    def __init__(self, content):
        self.content = content


@pytest.mark.asyncio
async def test_good_verdict_uses_first_retrieval():
    calls = {"retrieve": 0, "rewrite": 0}

    async def retrieve_fn(q):
        calls["retrieve"] += 1
        return [_Hit("ctx")]

    async def grade_fn(q, docs):
        return "good"

    async def rewrite_fn(q):
        calls["rewrite"] += 1
        return q + " more"

    out = await run_self_eval_loop(
        "query", retrieve_fn=retrieve_fn, grade_fn=grade_fn, rewrite_fn=rewrite_fn,
        max_retries=1,
    )

    assert [h.content for h in out.hits] == ["ctx"]
    assert out.verdict == "good"
    assert out.disclaimer is False
    assert out.rounds == 0
    assert calls["rewrite"] == 0
    assert calls["retrieve"] == 1


@pytest.mark.asyncio
async def test_bad_then_good_after_one_rewrite():
    verdicts = iter(["bad", "good"])
    retrievals = iter([[_Hit("v1")], [_Hit("v2")]])

    async def retrieve_fn(q):
        return next(retrievals)

    async def grade_fn(q, docs):
        return next(verdicts)

    rewrites = []

    async def rewrite_fn(q):
        rewrites.append(q)
        return "rewritten"

    out = await run_self_eval_loop(
        "query", retrieve_fn=retrieve_fn, grade_fn=grade_fn, rewrite_fn=rewrite_fn,
        max_retries=1,
    )

    assert [h.content for h in out.hits] == ["v2"]
    assert out.rounds == 1
    assert out.disclaimer is False
    assert len(rewrites) == 1


@pytest.mark.asyncio
async def test_empty_recall_skips_grader_cheap_gate():
    graded = []

    async def retrieve_fn(q):
        return []

    async def grade_fn(q, docs):
        graded.append(1)
        return "good"

    async def rewrite_fn(q):
        return "rw"

    out = await run_self_eval_loop(
        "q", retrieve_fn=retrieve_fn, grade_fn=grade_fn, rewrite_fn=rewrite_fn,
        max_retries=1,
    )

    # Cheap gate: the LLM grader is never called on empty recall.
    assert graded == []
    assert out.disclaimer is True


@pytest.mark.asyncio
async def test_partial_verdict_is_used_not_rewritten():
    rewrites = []

    async def retrieve_fn(q):
        return [_Hit("some context")]

    async def grade_fn(q, docs):
        return "partial"

    async def rewrite_fn(q):
        rewrites.append(q)
        return "rw"

    out = await run_self_eval_loop(
        "q", retrieve_fn=retrieve_fn, grade_fn=grade_fn, rewrite_fn=rewrite_fn,
        max_retries=1,
    )

    assert out.verdict == "partial"
    assert out.disclaimer is False
    assert rewrites == []


@pytest.mark.asyncio
async def test_exhausted_budget_sets_disclaimer():
    async def retrieve_fn(q):
        return [_Hit("x")]

    async def grade_fn(q, docs):
        return "bad"

    async def rewrite_fn(q):
        return "rw"

    out = await run_self_eval_loop(
        "q", retrieve_fn=retrieve_fn, grade_fn=grade_fn, rewrite_fn=rewrite_fn,
        max_retries=1,
    )

    assert out.disclaimer is True
    assert out.verdict == "bad"


@pytest.mark.asyncio
async def test_zero_retries_does_not_rewrite():
    rewrites = []

    async def retrieve_fn(q):
        return [_Hit("x")]

    async def grade_fn(q, docs):
        return "bad"

    async def rewrite_fn(q):
        rewrites.append(q)
        return "rw"

    out = await run_self_eval_loop(
        "q", retrieve_fn=retrieve_fn, grade_fn=grade_fn, rewrite_fn=rewrite_fn,
        max_retries=0,
    )

    assert rewrites == []
    assert out.disclaimer is True
