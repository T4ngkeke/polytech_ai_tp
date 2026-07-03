"""
test_ingestion_routing.py — [v7.1] deterministic ingestion routing by doc_type.

Type is known at upload, so the pipeline does not guess structure: CM (lectures)
take the Contextual-Retrieval chunk path; TD/TP (problem sets / labs) additionally
take the statement-extraction path. Pure mapping — no model, no DB.
"""

from backend.app.models import DocType
from backend.worker.routing import plan_for


def test_cm_gets_contextual_retrieval_no_exercises():
    plan = plan_for(DocType.CM)
    assert plan.contextual_retrieval is True
    assert plan.extract_exercises is False
    # [v7.3] strict split: concept RAG is fed by CM only.
    assert plan.produce_chunks is True


def test_td_extracts_exercises_no_contextual():
    plan = plan_for(DocType.TD)
    assert plan.extract_exercises is True
    assert plan.contextual_retrieval is False
    # [v7.3] strict split: exercises never enter the RAG chunk store.
    assert plan.produce_chunks is False


def test_tp_extracts_exercises_no_contextual():
    plan = plan_for(DocType.TP)
    assert plan.extract_exercises is True
    assert plan.contextual_retrieval is False
    assert plan.produce_chunks is False
