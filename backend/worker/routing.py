"""
routing.py — [v7.1] deterministic ingestion routing by document type.

`doc_type` is known at upload, so we never guess structure. [v7.3] Strict split:
concept questions are answered from CM chunks only (hybrid RAG) and exercises are
answered from the Exercises table only (agentic search) — so CM produces chunks
and never exercises, while TD/TP produce exercises and never chunks.
"""

from dataclasses import dataclass

from backend.app.models import DocType


@dataclass(frozen=True)
class IngestionPlan:
    """Per-type ingestion switches."""
    extract_exercises: bool
    contextual_retrieval: bool
    produce_chunks: bool


def plan_for(doc_type: DocType) -> IngestionPlan:
    """Map a document type to its ingestion plan."""
    if doc_type in (DocType.TD, DocType.TP):
        return IngestionPlan(
            extract_exercises=True, contextual_retrieval=False, produce_chunks=False,
        )
    # CM (and any default) — lecture material, often fragmentary slides.
    return IngestionPlan(
        extract_exercises=False, contextual_retrieval=True, produce_chunks=True,
    )
