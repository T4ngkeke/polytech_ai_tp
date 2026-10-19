"""
test_schema.py — the shape of the data model after the CM-only rewrite.

Replaces the three release-shaped schema files. The
model no longer has a document *type*: every upload is course material, so the
exercise / answer / hint half of the schema is gone and with it the routing
metadata that only existed to steer documents into it.

Structural assertions only — no DB round-trip needed.
"""

from backend.app.database import Base
from backend.app.models import (
    DocChunk,
    Document,
    DocumentStatus,
    IngestionJob,
    JobType,
    Message,
    MessageFeedback,
    Session,
)


def _cols(model) -> set[str]:
    return {c.name for c in model.__table__.columns}


# ---------------------------------------------------------------------------
# What was removed
# ---------------------------------------------------------------------------


def test_exercise_and_answer_tables_are_gone():
    """Exercises and Answers are removed outright, not emptied.

    Teachers now put per-exercise guidance in their class Rules instead, so
    there is nothing left for the tables to hold."""
    tables = set(Base.metadata.tables)
    assert "exercises" not in tables
    assert "answers" not in tables


def test_document_has_no_type_or_audience():
    """One upload path, one kind of document, visible to the whole class."""
    cols = _cols(Document)
    for gone in ("doc_type", "audience", "has_answers", "answers_for_document_id",
                 "segmentation_cache"):
        assert gone not in cols, f"Document still carries {gone}"


def test_docchunk_has_no_type_or_audience():
    """The denormalized filter columns went with the fields they mirrored."""
    cols = _cols(DocChunk)
    assert "doc_type" not in cols
    assert "audience" not in cols


def test_session_has_no_exercise_dialogue_state():
    """The sticky exercise and the clarify anti-loop both belonged to the
    exercise route, which no longer exists."""
    cols = _cols(Session)
    assert "current_exercise_id" not in cols
    assert "last_route" not in cols


def test_hint_and_answer_enums_are_gone():
    import backend.app.models as models

    for gone in ("HintStatus", "HintSource", "AnswerForm", "DocType", "Audience"):
        assert not hasattr(models, gone), f"models still exports {gone}"


def test_only_ingest_jobs_remain():
    """Hint generation was the only other job type."""
    assert {t.value for t in JobType} == {"ingest"}


# ---------------------------------------------------------------------------
# What survived
# ---------------------------------------------------------------------------


def test_document_keeps_ingestion_metadata():
    cols = _cols(Document)
    for kept in ("class_id", "lab_id", "filename", "storage_path", "content_hash",
                 "language", "ingest_report", "status", "error_message",
                 "page_count", "uploaded_by"):
        assert kept in cols, f"Document lost {kept}"


def test_needs_review_is_still_a_document_state():
    """A file the character-yield gate rejects is handed back to the teacher."""
    assert DocumentStatus.needs_review.value == "needs_review"


def test_docchunk_keeps_retrieval_columns():
    cols = _cols(DocChunk)
    for kept in ("content", "context", "section", "embedding", "tsv", "page_no",
                 "chunk_index", "class_id", "lab_id", "edited_by_teacher"):
        assert kept in cols, f"DocChunk lost {kept}"


def test_session_keeps_test_drive_flag():
    """Teachers still preview a lab by chatting in it."""
    assert "is_test" in _cols(Session)


def test_message_keeps_feedback():
    assert "feedback" in _cols(Message)
    assert {f.value for f in MessageFeedback} == {"up", "down"}


def test_ingestion_job_keeps_queue_columns():
    cols = _cols(IngestionJob)
    for kept in ("document_id", "status", "job_type", "payload", "priority",
                 "attempts", "locked_at", "error_message"):
        assert kept in cols, f"IngestionJob lost {kept}"


def test_old_chat_path_tables_are_gone():
    """The LangGraph chat path is gone: its learner profiles, router decisions and
    per-message traces are no longer models (old databases keep the tables)."""
    import backend.app.models as models
    for name in ("LearnerProfile", "RouterQueryLog", "AgentTraceLog", "CoachingLevel"):
        assert not hasattr(models, name), name
