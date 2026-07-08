"""
hints.py — [v8.0 §9] teacher-triggered hint generation (worker, plain Python).

Bone-level MVP (grilling 2026-07-07): classify the answer form, generate the three
tiers in a single shot (never revealing the final result), then gate with a
deterministic leak-lint + a 1-token LLM judge, bounded-retrying before giving up.

Deliberately NOT here (all data-triggered, added only when the offline eval shows
the MVP insufficient): rejection-sampling derivation, sympy/sandbox verifiers, the
`search_cm` bounded tool loop, best-of-n. Control flow stays in Python — not ReAct.

Every model call is an injected `*_fn` (prompt → raw reply) so this is unit-testable
with fakes and has no DB / HTTP dependency; persistence lands in §10.
"""

import json
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

from backend.app.models import AnswerForm, HintSource, HintStatus

# A hint-model call: takes an assembled prompt, returns the raw reply string.
HintLLMFn = Callable[[str], Awaitable[str]]

# Answer form → how the resulting hints were grounded.
_FORM_SOURCE = {
    AnswerForm.worked: HintSource.worked,          # full solution given → rewrite
    AnswerForm.final_only: HintSource.derived,      # result only → derived toward it
    AnswerForm.proof_no_process: HintSource.blind,  # no process → blind solve, red flag
}


@dataclass
class HintResult:
    """Outcome of one exercise's hint generation."""
    status: HintStatus          # pending_review (ready for teacher) or failed
    hint_source: HintSource
    hints: list[str] | None = None
    reason: str | None = None   # why it failed


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _parse_json_list(raw: str | None) -> list[str]:
    """Defensive parse of a JSON string array; anything else → []."""
    if not raw or not raw.strip():
        return []
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    return [s for s in data if isinstance(s, str)] if isinstance(data, list) else []


async def classify_answer_form(statement: str, answer: str, llm_fn: HintLLMFn) -> AnswerForm:
    """Classify an uploaded answer into its reliability tier. Unparseable → `worked`
    (the safe default: full text present, so no derivation is attempted)."""
    prompt = (
        "Classify this exercise answer into exactly one of: worked (a full worked "
        "solution with steps), final_only (only the final result, no steps), "
        "proof_no_process (a proof/claim with no derivation). Reply with just the "
        f"label.\n\nExercise:\n{statement}\n\nAnswer:\n{answer}"
    )
    raw = _norm(await llm_fn(prompt))
    # Check the most specific labels first (they contain the shorter ones' words).
    for form in (AnswerForm.proof_no_process, AnswerForm.final_only, AnswerForm.worked):
        if form.value in raw:
            return form
    return AnswerForm.worked


async def generate_hint_tiers(statement: str, answer: str, llm_fn: HintLLMFn) -> list[str]:
    """Single-shot L1/L2/L3 generation. The prompt forbids revealing the final
    result; leak_lint re-checks deterministically afterwards."""
    prompt = (
        "Write exactly three escalating hints (L1 a gentle nudge, L2 the method, "
        "L3 close but stopping short of the final result) for this exercise. NEVER "
        "state or compute the final answer. Reply as a JSON array of three strings.\n\n"
        f"Exercise:\n{statement}\n\nReference answer (do NOT reveal it):\n{answer}"
    )
    return _parse_json_list(await llm_fn(prompt))


def leak_lint(tiers: list[str], final_answer: str) -> bool:
    """Deterministic gate: True if any tier contains the final answer verbatim
    (normalized). Cheap first line of defence before the LLM judge."""
    fa = _norm(final_answer)
    if not fa:
        return False
    return any(fa in _norm(tier) for tier in tiers)


async def judge_hints(statement: str, tiers: list[str], llm_fn: HintLLMFn) -> bool:
    """1-token LLM verdict: are these hints helpful AND non-revealing? True = ok."""
    prompt = (
        "Do these tiered hints guide without revealing the final answer, and are they "
        "helpful? Answer with one word: good or bad.\n\n"
        f"Exercise:\n{statement}\n\nHints:\n" + "\n".join(tiers)
    )
    return _norm(await llm_fn(prompt)).startswith("good")


async def generate_hints_for_exercise(
    statement: str,
    answer: str,
    *,
    classify_fn: HintLLMFn,
    generate_fn: HintLLMFn,
    judge_fn: HintLLMFn,
    max_retries: int = 1,
) -> HintResult:
    """Orchestrate the MVP pipeline. Returns pending_review on success (teacher then
    reviews) or failed + reason after the bounded retries are exhausted."""
    form = await classify_answer_form(statement, answer, classify_fn)
    source = _FORM_SOURCE[form]

    reason = "generation failed"
    for _ in range(max_retries + 1):
        tiers = await generate_hint_tiers(statement, answer, generate_fn)
        if not tiers:
            reason = "empty generation"
            continue
        if leak_lint(tiers, answer):
            reason = "leak detected (a tier revealed the answer)"
            continue
        if not await judge_hints(statement, tiers, judge_fn):
            reason = "judge rejected the hints"
            continue
        return HintResult(status=HintStatus.pending_review, hint_source=source, hints=tiers)

    return HintResult(status=HintStatus.failed, hint_source=source, reason=reason)
