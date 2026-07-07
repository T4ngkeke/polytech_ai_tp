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
    """Per-type ingestion switches (three independent products)."""
    extract_exercises: bool
    contextual_retrieval: bool
    produce_chunks: bool
    produce_answers: bool = False


def plan_for(doc_type: DocType, has_answers: bool = False) -> IngestionPlan:
    """Map a document type (+ whether it carries answers) to its ingestion plan.

    [v8.0] Three products, never overlapping the strict split: CM → chunks only;
    TD/TP → exercises (+ answers when the doc carries them); a standalone corrigé
    → answers ONLY (no chunks, no exercises — it pairs to an existing TD's
    exercises by number, so re-extracting would duplicate rows)."""
    if doc_type == DocType.corrige:
        return IngestionPlan(
            extract_exercises=False, contextual_retrieval=False,
            produce_chunks=False, produce_answers=True,
        )
    if doc_type in (DocType.TD, DocType.TP):
        return IngestionPlan(
            extract_exercises=True, contextual_retrieval=False,
            produce_chunks=False, produce_answers=has_answers,
        )
    # CM (and any default) — lecture material, often fragmentary slides.
    return IngestionPlan(
        extract_exercises=False, contextual_retrieval=True,
        produce_chunks=True, produce_answers=False,
    )
