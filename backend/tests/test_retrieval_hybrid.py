"""
test_retrieval_hybrid.py — hybrid-retrieval pure logic.

Reciprocal-rank fusion (vector + BM25) and reranker application are pure functions
(no DB), so they are unit-tested directly. The SQL recall paths (vector ANN, BM25
tsvector, tenant filter) are exercised separately against live pgvector.
"""

import pytest

from backend.app.services.retrieval_service import (
    ChunkHit,
    apply_rerank,
    detect_language,
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
    """A failing reranker (bad/unreachable endpoint, malformed body) must
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


# --- rerank reads the augmented text -----------------------------------
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


def test_language_comes_from_the_text_french_on_a_tie():
    assert detect_language(["Une restriction garde les tuples qui vérifient la condition."]) == "fr"
    assert detect_language(["The selection keeps the tuples that satisfy the condition."]) == "en"
    # A French course with English code stays French; code alone is a tie.
    assert detect_language(["Le parcours de l'arbre est récursif.", "for node in tree: print(node)"]) == "fr"
    assert detect_language(["SELECT * FROM t;"]) == "fr"
