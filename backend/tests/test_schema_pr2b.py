"""
test_schema_pr2b.py — [v8.0 PR-2b] schema batch 2 (hint lifecycle + answers).

Additive column/table/enum guards for the hint system foundation. (The
Exercises.hints is now a JSON tiered array (§10); the generation
workflow first produces tiered data and its consumers are rewritten together.)
"""

from backend.app.models import (
    Answer,
    AnswerForm,
    DocType,
    Document,
    Exercise,
    HintSource,
    HintStatus,
    IngestionJob,
    JobType,
    Message,
    MessageFeedback,
)


def _cols(model) -> set[str]:
    return {c.name for c in model.__table__.columns}


def test_answers_table_columns():
    cols = _cols(Answer)
    for c in ("id", "exercise_id", "document_id", "number_raw", "number_normalized",
              "answer_form", "answer_text", "derivation_text", "verified",
              "created_at", "updated_at"):
        assert c in cols, f"Answer missing column {c}"


def test_exercise_hint_lifecycle_columns():
    cols = _cols(Exercise)
    assert "hint_status" in cols
    assert "hint_source" in cols


def test_document_answer_columns_and_corrige_type():
    cols = _cols(Document)
    assert "has_answers" in cols
    assert "answers_for_document_id" in cols
    # doc_type gains a standalone-answer document kind.
    assert DocType.corrige.value == "corrigé"


def test_message_feedback_column():
    assert "feedback" in _cols(Message)


def test_ingestion_job_type_and_payload():
    cols = _cols(IngestionJob)
    assert "job_type" in cols
    assert "payload" in cols


def test_pr2b_enum_values():
    assert {s.value for s in HintStatus} == {
        "none", "generating", "pending_review", "approved", "failed"}
    assert {s.value for s in HintSource} == {"worked", "derived", "blind", "none"}
    assert {s.value for s in AnswerForm} == {"worked", "final_only", "proof_no_process"}
    assert {s.value for s in JobType} == {"ingest", "hint_generate"}
    assert {s.value for s in MessageFeedback} == {"up", "down"}
