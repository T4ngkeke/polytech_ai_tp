"""
routing.py — [v7.1] deterministic ingestion routing by document type.

`doc_type` is known at upload, so we never guess structure. CM (lectures: slides /
book) are context-poor and take the Contextual-Retrieval chunk path; TD / TP (problem
sets / labs) additionally take the statement-extraction path. Every document is still
chunked for hybrid RAG — these flags only add per-type work.
"""

from dataclasses import dataclass

from backend.app.models import DocType


@dataclass(frozen=True)
class IngestionPlan:
    """Per-type ingestion switches. Chunking + embedding always happen."""
    extract_exercises: bool
    contextual_retrieval: bool


def plan_for(doc_type: DocType) -> IngestionPlan:
    """Map a document type to its ingestion plan."""
    if doc_type in (DocType.TD, DocType.TP):
        return IngestionPlan(extract_exercises=True, contextual_retrieval=False)
    # CM (and any default) — lecture material, often fragmentary slides.
    return IngestionPlan(extract_exercises=False, contextual_retrieval=True)
