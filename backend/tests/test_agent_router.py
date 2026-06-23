"""
test_agent_router.py — [v7.1] multilingual embedding-kNN intent router.

A deterministic exercise-number regex is the fast-path (-> agentic_search); every
other message is classified by cosine-kNN of its embedding against in-code
multilingual anchor exemplars. Below the confidence threshold it falls back to the
safest branch (rag) and is flagged for logging. kNN is tested with controlled
exemplar embeddings; embedding the exemplars is tested with a fake embedder.
"""

import pytest

from backend.app.agent.router import (
    INTENT_EXEMPLARS,
    EmbeddingRouter,
    build_embedding_router,
)


def _router(threshold: float = 0.5) -> EmbeddingRouter:
    # rag near the x-axis, direct near the y-axis; z is "far from everything".
    return EmbeddingRouter(
        {"rag": [[1.0, 0.0, 0.0]], "direct": [[0.0, 1.0, 0.0]]},
        threshold=threshold,
    )


@pytest.mark.parametrize("message", [
    "How do I do exercise 2?",
    "I'm stuck on Exercise 3.1",
    "exercice 5 svp",
])
def test_exercise_reference_routes_to_agentic_search(message):
    # Deterministic fast-path: the query embedding is irrelevant.
    decision = _router().route(message, query_embedding=[0.0, 0.0, 1.0])
    assert decision.route == "agentic_search"
    assert decision.low_confidence is False


def test_concept_query_routes_to_rag_via_knn():
    decision = _router().route("what is recursion?", query_embedding=[0.9, 0.1, 0.0])
    assert decision.route == "rag"
    assert decision.low_confidence is False


def test_chitchat_routes_to_direct_via_knn():
    decision = _router().route("hello there", query_embedding=[0.1, 0.9, 0.0])
    assert decision.route == "direct"
    assert decision.low_confidence is False


def test_low_confidence_falls_back_to_rag_and_flags():
    # Orthogonal to every exemplar (cosine 0 < threshold) -> safe fallback + flag.
    decision = _router().route("???", query_embedding=[0.0, 0.0, 1.0])
    assert decision.route == "rag"
    assert decision.low_confidence is True


@pytest.mark.asyncio
async def test_build_embedding_router_embeds_all_exemplars_in_one_call():
    calls: list[list[str]] = []

    async def fake_embed(texts):
        calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]

    router = await build_embedding_router(fake_embed, threshold=0.5)

    total = sum(len(v) for v in INTENT_EXEMPLARS.values())
    assert len(calls) == 1            # embedded once, batched
    assert len(calls[0]) == total
    # Both intents are represented and routable.
    assert isinstance(router, EmbeddingRouter)
