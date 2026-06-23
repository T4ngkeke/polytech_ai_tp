"""
test_schema_v71.py — v7.1 schema-shape regression.

These assert the v7.1 data-model decisions structurally (no live DB needed):
  * Red line: there is NO `solution` column anywhere — the corrigé is not stored.
  * Documents carry deterministic routing metadata (`doc_type`, `audience`) and a
    `needs_review` state.
  * DocChunks carry Contextual-Retrieval (`context`), `section`, denormalized
    `doc_type`/`audience`, and a BM25 `tsv` column.
  * A `RouterQueryLog` table exists for low-confidence routing telemetry.
"""

from backend.app.models import (
    Audience,
    DocChunk,
    DocType,
    Document,
    DocumentStatus,
    Exercise,
    RouterQueryLog,
)


def test_exercise_has_no_solution_column():
    # Red line: the corrigé is never stored — the column must not exist at all.
    assert "solution" not in Exercise.__table__.columns


def test_document_status_has_needs_review():
    # A PDF the char-yield gate rejects is flagged needs_review, not silently ingested.
    assert DocumentStatus.needs_review.value == "needs_review"


def test_document_has_doc_type_and_audience():
    # Deterministic routing metadata, set at upload.
    assert "doc_type" in Document.__table__.columns
    assert "audience" in Document.__table__.columns
    assert "page_count" in Document.__table__.columns
    assert {t.value for t in DocType} == {"CM", "TD", "TP"}
    assert {a.value for a in Audience} == {"student", "teacher"}


def test_docchunk_has_v71_retrieval_columns():
    cols = DocChunk.__table__.columns
    # Contextual Retrieval: generated context stored separately from the original.
    assert "context" in cols
    assert "section" in cols
    # Denormalized routing/audience filter lives on the chunk for filter-without-JOIN.
    assert "doc_type" in cols
    assert "audience" in cols
    # BM25 full-text column.
    assert "tsv" in cols


def test_router_query_log_table_exists():
    cols = RouterQueryLog.__table__.columns
    assert "message" in cols
    assert "chosen_route" in cols
    assert "top_similarity" in cols
