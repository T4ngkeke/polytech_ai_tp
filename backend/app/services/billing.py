"""
billing.py — [v7.2] weighted-token quota accounting.

Prefill (prompt) tokens are cheaper to serve than decode (completion) tokens, so
the daily quota is charged on a weighted sum rather than the raw total:

    billed = round(prompt_tokens * alpha + completion_tokens * beta)

``alpha`` / ``beta`` come from SystemConfigs (``TOKEN_ALPHA`` / ``TOKEN_BETA``,
default 0.2 / 1.0) and are admin-configurable. Raw prompt/completion counts are
still stored per message for auditing; only the quota accumulation is weighted.
"""

from __future__ import annotations


def compute_billed_tokens(
    prompt_tokens: int,
    completion_tokens: int,
    *,
    alpha: float,
    beta: float,
) -> int:
    """Return the integer billed-token cost of one exchange."""
    billed = prompt_tokens * alpha + completion_tokens * beta
    return max(0, round(billed))
