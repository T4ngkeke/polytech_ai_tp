"""
test_retrieval_hybrid.py — [v7.1] hybrid-retrieval pure logic.

Reciprocal-rank fusion (vector + BM25) and reranker application are pure functions
(no DB), so they are unit-tested directly. The SQL recall paths (vector ANN, BM25
tsvector, tenant/audience filter) are exercised separately against live pgvector.
"""

import pytest

from backend.app.services.retrieval_service import (
    ChunkHit,
    apply_rerank,
    reciprocal_rank_fusion,
)


def _hit(content: str, context: str | None = None) -> ChunkHit:
    return ChunkHit(content=content, page_no=1, document_id=None, context=context)


def test_rrf_ranks_items_high_in_both_lists_first():
    vector_ids = ["a", "b", "c"]
    bm25_ids = ["b", "a", "d"]

    fused = reciprocal_rank_fusion([vector_ids, bm25_ids])
    order = [item for item, _score in fused]

    # 'a' and 'b' appear near the top of both lists → they win.
    assert set(order[:2]) == {"a", "b"}
    # The union of both lists is represented.
    assert set(order) == {"a", "b", "c", "d"}


def test_rrf_single_list_preserves_order():
    fused = reciprocal_rank_fusion([["x", "y", "z"]])
    assert [item for item, _ in fused] == ["x", "y", "z"]


@pytest.mark.asyncio
async def test_apply_rerank_orders_by_score_and_truncates():
    hits = [_hit("aaa"), _hit("bbb"), _hit("ccc")]

    async def fake_rerank(query, documents):
        # Prefer documents mentioning 'b'.
        return [1.0 if "b" in d else 0.0 for d in documents]

    out, all_filtered = await apply_rerank("q", hits, fake_rerank, top_k=2)

    assert [h.content for h in out] == ["bbb", "aaa"]  # bbb first; ties keep order
    assert len(out) == 2
    assert all_filtered is False


@pytest.mark.asyncio
async def test_apply_rerank_degrades_to_fusion_order_when_reranker_fails():
    """[v7.2] A failing reranker (bad/unreachable endpoint, malformed body) must
    never break retrieval: it degrades to the incoming fusion order, truncated to
    top_k — the same path as 'rerank unset'."""
    hits = [_hit("aaa"), _hit("bbb"), _hit("ccc")]

    async def boom_rerank(query, documents):
        raise RuntimeError("404 Not Found for rerank endpoint")

    out, all_filtered = await apply_rerank("q", hits, boom_rerank, top_k=2)

    # Fusion order preserved (no reranking applied), truncated to top_k.
    assert [h.content for h in out] == ["aaa", "bbb"]
    assert len(out) == 2
    assert all_filtered is False


# --- [v7.3] rerank reads the augmented text -----------------------------------
# Recall matched on context+content; scoring the bare content would veto exactly
# the context-poor slide chunks that Contextual Retrieval was built to save.

@pytest.mark.asyncio
async def test_apply_rerank_scores_augmented_text():
    hits = [_hit("fragment", context="CONTEXT: binary coding chapter")]
    seen_docs: list[str] = []

    async def capturing_rerank(query, documents):
        seen_docs.extend(documents)
        return [1.0 for _ in documents]

    await apply_rerank("q", hits, capturing_rerank, top_k=1)

    assert len(seen_docs) == 1
    assert "CONTEXT: binary coding chapter" in seen_docs[0]
    assert "fragment" in seen_docs[0]


# --- [v7.3] score threshold gates injection ------------------------------------
# Vector top-k always returns *something*; nearest != relevant. Below-threshold
# hits are dropped; nothing surviving signals the zero-context disclaimer branch.

@pytest.mark.asyncio
async def test_threshold_drops_low_scores():
    hits = [_hit("good"), _hit("garbage")]

    async def scoring_rerank(query, documents):
        return [0.9 if "good" in d else 0.1 for d in documents]

    out, all_filtered = await apply_rerank(
        "q", hits, scoring_rerank, top_k=5, score_threshold=0.5,
    )

    assert [h.content for h in out] == ["good"]
    assert all_filtered is False


@pytest.mark.asyncio
async def test_threshold_all_filtered_signals_disclaimer_branch():
    hits = [_hit("garbage one"), _hit("garbage two")]

    async def scoring_rerank(query, documents):
        return [0.1 for _ in documents]

    out, all_filtered = await apply_rerank(
        "q", hits, scoring_rerank, top_k=5, score_threshold=0.5,
    )

    assert out == []
    assert all_filtered is True


@pytest.mark.asyncio
async def test_threshold_none_is_off_by_default():
    """Ships disabled: behaviour identical to v7.2 until calibrated (Phase 3)."""
    hits = [_hit("anything")]

    async def low_scores(query, documents):
        return [0.0 for _ in documents]

    out, all_filtered = await apply_rerank("q", hits, low_scores, top_k=5)

    assert [h.content for h in out] == ["anything"]
    assert all_filtered is False


@pytest.mark.asyncio
async def test_threshold_skipped_when_reranker_fails():
    """No scores → no gate: degrade to fusion order rather than filtering blind."""
    hits = [_hit("aaa")]

    async def boom_rerank(query, documents):
        raise RuntimeError("rerank down")

    out, all_filtered = await apply_rerank(
        "q", hits, boom_rerank, top_k=5, score_threshold=0.9,
    )

    assert [h.content for h in out] == ["aaa"]
    assert all_filtered is False
