"""
selfeval.py — [v7.1] bounded self-eval retrieval loop.

At ~60 tps the *grade* is nearly free (a short guided verdict); the expensive part
is re-generation, so the loop is bounded. Design:

  retrieve → cheap gate (empty recall = bad, no LLM) → 3-tier guided verdict
  (good/partial/bad) → good|partial use as-is; bad rewrites the query and
  re-retrieves, at most `max_retries` rounds → still bad ⇒ raise the disclaimer.

Pure orchestration over injected functions, so it is testable without a model.
"""

from dataclasses import dataclass
from typing import Awaitable, Callable

RetrieveFn = Callable[[str], Awaitable[list]]
GradeFn = Callable[[str, list[str]], Awaitable[str]]
RewriteFn = Callable[[str], Awaitable[str]]

_VERDICTS = {"good", "partial", "bad"}


def _normalize(verdict: str) -> str:
    """Coerce the grader output to the controlled vocabulary (default: partial)."""
    v = (verdict or "").strip().lower()
    return v if v in _VERDICTS else "partial"


@dataclass(frozen=True)
class RetrievalOutcome:
    """Result of the self-eval loop."""
    hits: list
    verdict: str
    rounds: int          # how many rewrites were performed
    disclaimer: bool     # answer should be tagged "not enough material"


async def run_self_eval_loop(
    query: str,
    *,
    retrieve_fn: RetrieveFn,
    grade_fn: GradeFn,
    rewrite_fn: RewriteFn,
    max_retries: int = 1,
) -> RetrievalOutcome:
    """Retrieve, self-grade, and bounded-re-retrieve until good/partial or budget out."""
    current_query = query
    hits: list = []

    for attempt in range(max_retries + 1):
        hits = await retrieve_fn(current_query)

        if not hits:
            verdict = "bad"  # cheap gate: empty recall, never call the grader
        else:
            verdict = _normalize(await grade_fn(query, [getattr(h, "content", h) for h in hits]))

        if verdict in ("good", "partial"):
            return RetrievalOutcome(hits=hits, verdict=verdict, rounds=attempt, disclaimer=False)

        # bad → rewrite and retry while budget remains.
        if attempt < max_retries:
            current_query = await rewrite_fn(query)

    # Budget exhausted, still bad: surface what we have but flag a disclaimer.
    return RetrievalOutcome(hits=hits, verdict="bad", rounds=max_retries, disclaimer=True)
