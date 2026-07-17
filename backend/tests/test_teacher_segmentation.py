"""
test_teacher_segmentation.py — [v8.1] segmentation human-confirm.

When ingest stored two candidate segmentations (regex vs LLM disagreement), the
teacher can GET both side by side and POST a choice. Choosing the alternate
rebuilds the document's exercises deterministically from the stored segments —
no LLM call, no re-ingest: hints are carried over by number (approved demoted
to pending_review — the statement changed, so it must be re-reviewed), and
teacher-edited exercises survive verbatim. The cache swaps so a later
re-ingest respects the choice.
"""

import uuid

import pytest

from backend.app.models import (
    Class, Document, DocType, DocumentStatus, Exercise,
    HintSource, HintStatus, Lab, UserRole,
)
from backend.tests.conftest import make_client, make_user


async def _seed_disagreeing_doc(db_session):
    """A TD live on the 2-boundary LLM split, with a 3-boundary regex alternate."""
    teacher = make_user(role=UserRole.teacher)
    db_session.add(teacher)
    await db_session.flush()
    cls = Class(id=uuid.uuid4(), name="Algo", teacher_id=teacher.id,
                invite_code=uuid.uuid4().hex[:6])
    db_session.add(cls)
    await db_session.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name="Lab")
    db_session.add(lab)
    await db_session.flush()

    llm_segments = [
        {"section": "Exercice 1", "content": "Exercice 1\nSum.\nExercice 2\nSort.",
         "page_no": 1},
        {"section": "Exercice 3", "content": "Exercice 3\nProve.", "page_no": 1},
    ]
    regex_segments = [
        {"section": "Exercice 1", "content": "Exercice 1\nSum.", "page_no": 1},
        {"section": "Exercice 2", "content": "Exercice 2\nSort.", "page_no": 1},
        {"section": "Exercice 3", "content": "Exercice 3\nProve.", "page_no": 1},
    ]
    doc = Document(
        id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="td.pdf",
        storage_path="/x", content_hash="h1", uploaded_by=teacher.id,
        doc_type=DocType.TD, status=DocumentStatus.indexed,
        ingest_report={"segmentation_disagreement": True, "exercise_count": 2},
        segmentation_cache={
            "extractor_version": "v8.0-2", "content_hash": "h1",
            "boundary_disagreements": 1, "dropped_invalid_lines": 0,
            "segments": llm_segments, "chosen": "llm",
            "disagreement": True, "alternate_segments": regex_segments,
        },
    )
    db_session.add(doc)
    await db_session.flush()

    ex1 = Exercise(id=uuid.uuid4(), document_id=doc.id, class_id=cls.id,
                   lab_id=lab.id, number="Exercice 1", number_normalized=1,
                   statement="Exercice 1\nSum.\nExercice 2\nSort.",
                   hints=["h1", "h2", "h3"], hint_status=HintStatus.approved,
                   hint_source=HintSource.worked)
    ex3 = Exercise(id=uuid.uuid4(), document_id=doc.id, class_id=cls.id,
                   lab_id=lab.id, number="Exercice 3", number_normalized=3,
                   statement="Ma version corrigée à la main.",
                   edited_by_teacher=True)
    db_session.add_all([ex1, ex3])
    await db_session.commit()
    return teacher, doc


@pytest.mark.asyncio
async def test_get_segmentation_returns_both_candidates(db_session):
    teacher, doc = await _seed_disagreeing_doc(db_session)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.get(f"/api/teacher/documents/{doc.id}/segmentation")
    finally:
        await client.aclose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["chosen"] == "llm"
    assert body["disagreement"] is True
    assert [s["section"] for s in body["current"]] == ["Exercice 1", "Exercice 3"]
    assert [s["section"] for s in body["alternate"]] == [
        "Exercice 1", "Exercice 2", "Exercice 3"]


@pytest.mark.asyncio
async def test_choose_alternate_rebuilds_carries_and_protects(db_session):
    teacher, doc = await _seed_disagreeing_doc(db_session)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.post(
            f"/api/teacher/documents/{doc.id}/segmentation/choose",
            json={"which": "regex"},
        )
    finally:
        await client.aclose()

    assert resp.status_code == 200

    from sqlalchemy import select
    exercises = (await db_session.execute(
        select(Exercise).where(Exercise.document_id == doc.id)
        .order_by(Exercise.number_normalized)
    )).scalars().all()
    assert [e.number_normalized for e in exercises] == [1, 2, 3]

    # Hints carried by number — approved is DEMOTED to pending_review
    # (statement changed → teacher must re-review).
    assert exercises[0].hints == ["h1", "h2", "h3"]
    assert exercises[0].hint_status == HintStatus.pending_review
    # A brand-new exercise starts clean.
    assert exercises[1].hints is None
    assert exercises[1].hint_status == HintStatus.none
    # Teacher-edited exercise survives verbatim.
    assert exercises[2].statement == "Ma version corrigée à la main."
    assert exercises[2].edited_by_teacher is True

    await db_session.refresh(doc)
    cache = doc.segmentation_cache
    assert cache["chosen"] == "regex"
    assert [s["section"] for s in cache["segments"]] == [
        "Exercice 1", "Exercice 2", "Exercice 3"]
    assert [s["section"] for s in cache["alternate_segments"]] == [
        "Exercice 1", "Exercice 3"]
    # The teacher's confirmation is recorded — the UI can stop warning.
    assert doc.ingest_report["segmentation_confirmed"] is True


@pytest.mark.asyncio
async def test_choose_current_is_a_confirming_noop(db_session):
    """Confirming the already-live split rebuilds nothing but records the
    confirmation (the warning badge can clear)."""
    teacher, doc = await _seed_disagreeing_doc(db_session)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.post(
            f"/api/teacher/documents/{doc.id}/segmentation/choose",
            json={"which": "llm"},
        )
    finally:
        await client.aclose()

    assert resp.status_code == 200
    from sqlalchemy import select
    exercises = (await db_session.execute(
        select(Exercise).where(Exercise.document_id == doc.id)
    )).scalars().all()
    assert len(exercises) == 2                     # untouched
    assert {e.hint_status for e in exercises} == {
        HintStatus.approved, HintStatus.none}      # approval NOT demoted
    await db_session.refresh(doc)
    assert doc.ingest_report["segmentation_confirmed"] is True


@pytest.mark.asyncio
async def test_foreign_teacher_cannot_touch_segmentation(db_session):
    teacher, doc = await _seed_disagreeing_doc(db_session)
    stranger = make_user(role=UserRole.teacher, username="stranger")
    db_session.add(stranger)
    await db_session.commit()
    client = await make_client(db_session, stranger)
    try:
        get_resp = await client.get(f"/api/teacher/documents/{doc.id}/segmentation")
        post_resp = await client.post(
            f"/api/teacher/documents/{doc.id}/segmentation/choose",
            json={"which": "regex"},
        )
    finally:
        await client.aclose()
    assert get_resp.status_code in (403, 404)
    assert post_resp.status_code in (403, 404)
