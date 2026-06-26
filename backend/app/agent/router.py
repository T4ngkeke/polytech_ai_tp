"""
router.py — [v7.1] multilingual embedding-kNN intent router.

Two layers, cheapest first:
  1. A deterministic exercise-number regex fast-path → `agentic_search` (no decode).
  2. Cosine-kNN of the query embedding against in-code multilingual anchor exemplars
     decides `rag` vs `direct`. Below a confidence threshold it falls back to the
     safest branch (`rag`) and flags the query (logged for a future BERT router).

Exemplars are embedded once (see `build_embedding_router`); routing itself is pure.
"""

import math
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]

# A specific exercise reference: keyword + number (EN/FR variants).
_EXERCISE_RE = re.compile(
    r"\b(exercise|exercice|exo|problem|probleme|question|task|练习|习题|题)\s*#?\s*\d",
    re.IGNORECASE,
)

# [v7.2] Exercise-shaped but without an arabic digit (Roman / Chinese numeral /
# implicit phrasing). When the fast-path misses but this matches, the graph asks
# a cheap ROUTER_MODEL to extract the intended exercise number.
_EXERCISE_KEYWORD_RE = re.compile(
    r"(exercise|exercice|exo\b|probl[eè]me|problem|练习|习题|题)",
    re.IGNORECASE,
)


def looks_like_exercise(message: str) -> bool:
    """True if the message references an exercise but the strict digit fast-path
    missed it — the trigger for the LLM exercise-number fallback."""
    if _EXERCISE_RE.search(message):
        return False  # already handled deterministically; no LLM needed
    return bool(_EXERCISE_KEYWORD_RE.search(message))


# Multilingual anchor exemplars per intent (zh/fr/en). Concept questions → rag;
# chit-chat / meta / acknowledgements → direct. Kept short and resident.
INTENT_EXEMPLARS: dict[str, list[str]] = {
    "rag": [
        "What is multithreading?",
        "Explain how pointers work",
        "What's the difference between a list and a tuple?",
        "qu'est-ce que l'héritage en programmation ?",
        "explique la récursivité",
        "c'est quoi un mutex ?",
        "什么是多线程？",
        "解释一下指针是怎么工作的",
        "面向对象编程是什么意思？",
    ],
    "direct": [
        "hello!",
        "thanks, that helped",
        "ok continue",
        "bonjour, ça va ?",
        "merci beaucoup",
        "qu'est-ce que tu peux faire ?",
        "你好",
        "谢谢你的帮助",
        "好的，继续",
    ],
}


@dataclass(frozen=True)
class RouteDecision:
    """Routing outcome. `low_confidence` marks a below-threshold kNN fallback."""
    route: str
    top_similarity: float
    low_confidence: bool


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class EmbeddingRouter:
    """Routes a message by exercise regex, then cosine-kNN over exemplar embeddings."""

    def __init__(
        self,
        exemplar_embeddings: dict[str, list[list[float]]],
        threshold: float,
    ):
        # Flatten to (intent, vector) pairs for a single nearest-neighbour scan.
        self._items: list[tuple[str, list[float]]] = [
            (intent, vec)
            for intent, vectors in exemplar_embeddings.items()
            for vec in vectors
        ]
        self._threshold = threshold

    def route(self, message: str, query_embedding: list[float]) -> RouteDecision:
        if _EXERCISE_RE.search(message):
            return RouteDecision("agentic_search", 1.0, False)

        best_intent, best_sim = "rag", -1.0
        for intent, vec in self._items:
            sim = _cosine(query_embedding, vec)
            if sim > best_sim:
                best_sim, best_intent = sim, intent

        if best_sim < self._threshold:
            # Ambiguous → safest branch, and flag it for telemetry.
            return RouteDecision("rag", best_sim, True)
        return RouteDecision(best_intent, best_sim, False)


# Module cache so production embeds the exemplars once per embedding model rather
# than on every request. Tests pass no `embedding_model` → always rebuild (isolated).
_EXEMPLAR_CACHE: dict[str, dict[str, list[list[float]]]] = {}


async def _embed_exemplars(embed_fn: EmbedFn) -> dict[str, list[list[float]]]:
    texts: list[str] = []
    labels: list[str] = []
    for intent, exemplars in INTENT_EXEMPLARS.items():
        for text in exemplars:
            texts.append(text)
            labels.append(intent)
    vectors = await embed_fn(texts)
    grouped: dict[str, list[list[float]]] = {}
    for label, vector in zip(labels, vectors):
        grouped.setdefault(label, []).append(vector)
    return grouped


async def build_embedding_router(
    embed_fn: EmbedFn,
    threshold: float,
    *,
    embedding_model: str | None = None,
) -> EmbeddingRouter:
    """Build a router, embedding the anchor exemplars once (cached per model)."""
    if embedding_model and embedding_model in _EXEMPLAR_CACHE:
        grouped = _EXEMPLAR_CACHE[embedding_model]
    else:
        grouped = await _embed_exemplars(embed_fn)
        if embedding_model:
            _EXEMPLAR_CACHE[embedding_model] = grouped
    return EmbeddingRouter(grouped, threshold)
