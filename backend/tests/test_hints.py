"""
test_hints.py — [v8.0 §9] hint generation MVP (worker/hints.py).

Bone-level pipeline (grilling 2026-07-07): classify answer form → single-shot tiered
generation → deterministic leak-lint + 1-token LLM judge → bounded retry → failed.
No rejection sampling / derivation / verifier / sandbox / search_cm — all deferred
(data-triggered). All model calls are injected `*_fn` and faked here.
"""

import pytest

from backend.app.models import AnswerForm, HintSource, HintStatus
from backend.worker.hints import (
    HintResult,
    classify_answer_form,
    generate_hints_for_exercise,
    generate_hint_tiers,
    judge_hints,
    leak_lint,
)


def _fake(reply: str):
    async def fn(prompt: str) -> str:
        return reply
    return fn


@pytest.mark.asyncio
async def test_classify_answer_form_maps_each_kind():
    for reply, expected in [("worked", AnswerForm.worked),
                            ("final_only", AnswerForm.final_only),
                            ("proof_no_process", AnswerForm.proof_no_process)]:
        assert await classify_answer_form("stmt", "ans", _fake(reply)) == expected


@pytest.mark.asyncio
async def test_classify_answer_form_defaults_worked_on_garbage():
    # Unparseable → treat as worked (full text present, no derivation needed).
    assert await classify_answer_form("s", "a", _fake("???")) == AnswerForm.worked


@pytest.mark.asyncio
async def test_generate_hint_tiers_parses_json_list():
    tiers = await generate_hint_tiers("statement", "answer", _fake('["L1", "L2", "L3"]'))
    assert tiers == ["L1", "L2", "L3"]


@pytest.mark.asyncio
async def test_generate_hint_tiers_defensive_on_garbage():
    assert await generate_hint_tiers("s", "a", _fake("not json")) == []


def test_leak_lint_flags_answer_in_a_tier():
    assert leak_lint(["think about it", "the answer is 42"], "42") is True


def test_leak_lint_clean_when_no_overlap():
    assert leak_lint(["consider the loop invariant", "you're close"], "42") is False


@pytest.mark.asyncio
async def test_judge_hints_verdict():
    assert await judge_hints("s", ["h"], _fake("good")) is True
    assert await judge_hints("s", ["h"], _fake("bad")) is False


@pytest.mark.asyncio
async def test_orchestrator_happy_path_pending_review():
    result = await generate_hints_for_exercise(
        "statement", "42",
        classify_fn=_fake("worked"),
        generate_fn=_fake('["nudge", "method", "close but no result"]'),
        judge_fn=_fake("good"),
    )
    assert isinstance(result, HintResult)
    assert result.status == HintStatus.pending_review
    assert result.hints == ["nudge", "method", "close but no result"]
    assert result.hint_source == HintSource.worked


@pytest.mark.asyncio
async def test_orchestrator_leak_exhausts_retries_and_fails():
    # The generator always leaks the answer → lint catches every attempt → failed.
    result = await generate_hints_for_exercise(
        "statement", "42",
        classify_fn=_fake("final_only"),
        generate_fn=_fake('["hint", "the answer is 42"]'),
        judge_fn=_fake("good"),
        max_retries=1,
    )
    assert result.status == HintStatus.failed
    assert result.hints is None
    assert "leak" in (result.reason or "").lower()


@pytest.mark.asyncio
async def test_orchestrator_final_only_source_is_derived():
    result = await generate_hints_for_exercise(
        "statement", "42",
        classify_fn=_fake("final_only"),
        generate_fn=_fake('["a", "b", "c"]'),
        judge_fn=_fake("good"),
    )
    assert result.hint_source == HintSource.derived


@pytest.mark.asyncio
async def test_orchestrator_proof_source_is_blind():
    result = await generate_hints_for_exercise(
        "prove X", "the proof",
        classify_fn=_fake("proof_no_process"),
        generate_fn=_fake('["a", "b", "c"]'),
        judge_fn=_fake("good"),
    )
    assert result.hint_source == HintSource.blind
    assert result.status == HintStatus.pending_review
