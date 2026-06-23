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


def _hit(content: str) -> ChunkHit:
    return ChunkHit(content=content, page_no=1, document_id=None)


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

    out = await apply_rerank("q", hits, fake_rerank, top_k=2)

    assert [h.content for h in out] == ["bbb", "aaa"]  # bbb first; ties keep order
    assert len(out) == 2
