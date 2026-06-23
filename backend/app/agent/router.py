"""
router.py — [v7] intent router for the chat agent.

Rules-first classification (cheap, no LLM) per the v7 design: obvious intents are
caught by regex so slow-bandwidth decodes aren't spent on routing.

Routes:
  * agentic_search — references a specific exercise ("how to do exercise 2?")
  * rag            — conceptual question ("what is multithreading?")
  * direct         — everything else (chit-chat, follow-ups)
"""

import re

Route = str  # "agentic_search" | "rag" | "direct"

# A specific exercise reference: keyword + number (EN/FR variants).
_EXERCISE_RE = re.compile(
    r"\b(exercise|exercice|exo|problem|question|task|练习|习题|题)\s*#?\s*\d",
    re.IGNORECASE,
)

# Conceptual-question openers (EN/FR).
_CONCEPT_RE = re.compile(
    r"\b(what is|what are|what'?s|explain|define|definition of|difference between|"
    r"how does|how do .* work|why is|why does|qu'?est[- ]ce|c'?est quoi|"
    r"comment fonctionne|pourquoi)\b",
    re.IGNORECASE,
)


def classify_intent(message: str) -> Route:
    """Classify a student message into a retrieval route."""
    if _EXERCISE_RE.search(message):
        return "agentic_search"
    if _CONCEPT_RE.search(message):
        return "rag"
    return "direct"
