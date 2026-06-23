"""
effort.py — [v7] per-message effort / clarity score in [0, 1].

Rules-first (no LLM). Rewards signals of thinking over a bare copy-paste:
  * an attempt / reasoning ("I tried", "I think", "because")
  * a specific exercise / chapter reference
  * a concrete error mentioned
  * actual prose (not just a pasted block + "debug")

This score feeds the smoothed sliding window per (student, lab).
"""

import re

_ATTEMPT_RE = re.compile(
    r"\b(i think|i tried|i'?ve tried|i got|i wrote|i used|i expected|my approach|"
    r"because|i'?m getting|i believe|je pense|j'?ai essayé|parce que|why does|why is)\b",
    re.IGNORECASE,
)
_EXERCISE_RE = re.compile(
    r"\b(exercise|exercice|exo|chapter|chapitre|problem|question|task)\s*#?\s*\d",
    re.IGNORECASE,
)
_ERROR_RE = re.compile(
    r"(error|exception|traceback|\bbug\b|\bfails?\b|erreur|segfault)",
    re.IGNORECASE,
)
# Fenced code block, or a line that looks like code.
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)


def _prose_word_count(message: str) -> int:
    """Words outside fenced code blocks."""
    prose = _CODE_BLOCK_RE.sub(" ", message)
    return len(re.findall(r"[A-Za-zÀ-ÿ']+", prose))


def assess_effort(message: str) -> float:
    score = 0.0
    if _ATTEMPT_RE.search(message):
        score += 0.35
    if _EXERCISE_RE.search(message):
        score += 0.20
    if _ERROR_RE.search(message):
        score += 0.20
    if _prose_word_count(message) >= 12:
        score += 0.25
    return max(0.0, min(1.0, score))
