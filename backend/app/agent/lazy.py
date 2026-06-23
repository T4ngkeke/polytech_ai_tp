"""
lazy.py — [v7] lazy / answer-seeking detector for the tutoring guardrail.

A message is "answer-seeking" when it DEMANDS the answer AND shows none of the
student's own attempt or reasoning. This keys on absence of effort, not on
merely mentioning an answer or exercise number — so a thinking question like
"I think the answer is X because Y, right?" is NOT flagged.

Rules-first (no LLM), EN + FR.
"""

import re

# Demands for the answer / for the work to be done for them.
_ANSWER_DEMAND_RE = re.compile(
    r"(give me|just give|tell me|what'?s|what is)\s+(the\s+)?(answer|solution)|"
    r"\b(the\s+)?(answer|solution)\s+(to|for|of|de|à)\b|"
    r"\b(solve|do|complete|finish|fais|résous|résoudre)\b[^?]*\bfor me\b|"
    r"\b(solve|do)\s+(this|it|exercise|exercice|question|problem)\b|"
    r"donne[- ]moi\s+(la\s+)?(réponse|solution)|"
    r"\bfais\b.*\b(exercice|exo|question)\b",
    re.IGNORECASE,
)

# Signals the student did their own thinking / made an attempt (EN + FR).
_ATTEMPT_RE = re.compile(
    r"\b(i think|i tried|i'?ve tried|i got|i wrote|i used|i expected|my approach|"
    r"because|i'?m getting|i believe|i guess|je pense|j'?ai essayé|j'?ai|parce que|"
    r"i assume|should it be|is that right|is this correct)\b",
    re.IGNORECASE,
)


def detect_answer_seeking(message: str) -> bool:
    """True when the message demands the answer with no sign of own effort."""
    if not _ANSWER_DEMAND_RE.search(message):
        return False
    return not _ATTEMPT_RE.search(message)
