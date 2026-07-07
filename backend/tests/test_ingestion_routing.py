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


# [v8.0] Third dimension: answers. CM none; TD/TP+has_answers add answers; a
# standalone corrigé produces ONLY answers (no chunks, no exercises of its own).

def test_cm_produces_no_answers():
    assert plan_for(DocType.CM).produce_answers is False


def test_td_without_answers_produces_no_answers():
    plan = plan_for(DocType.TD, has_answers=False)
    assert plan.extract_exercises is True
    assert plan.produce_answers is False


def test_td_with_answers_produces_exercises_and_answers():
    plan = plan_for(DocType.TD, has_answers=True)
    assert plan.extract_exercises is True
    assert plan.produce_answers is True
    assert plan.produce_chunks is False


def test_corrige_produces_answers_only():
    plan = plan_for(DocType.corrige)
    assert plan.produce_answers is True
    assert plan.extract_exercises is False   # it pairs to a TD, not its own exercises
    assert plan.produce_chunks is False      # never RAG chunks
    assert plan.contextual_retrieval is False
