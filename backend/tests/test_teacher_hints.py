"""
test_teacher_hints.py — [v8.0 §10] teacher hint-generation endpoints.

POST /documents/{id}/generate-hints          (batch — only none exercises)
POST /documents/{id}/exercises/{eid}/generate-hints  (single — override entry)

Both mark targets `generating` and enqueue a hint_generate job carrying the
exercise ids; the worker runner fulfils it. Batch is idempotent: it skips
pending_review/approved/edited exercises. Runs on SQLite via make_client.
"""

import uuid

import pytest
from sqlalchemy import select

from backend.app.models import (
    Audience, Class, Document, DocType, Exercise,
    HintStatus, IngestionJob, JobType, Lab, UserRole,
)
from backend.tests.conftest import make_client, make_user


async def _seed_doc_with_exercises(session):
    teacher = make_user(role=UserRole.teacher)
    session.add(teacher)
    await session.flush()
    cls = Class(id=uuid.uuid4(), name="Algo", teacher_id=teacher.id,
                invite_code=uuid.uuid4().hex[:6])
    session.add(cls)
    await session.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name="Lab 1")
    session.add(lab)
    await session.flush()
    doc = Document(id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="td.pdf",
                   storage_path="/x", content_hash=uuid.uuid4().hex,
                   uploaded_by=teacher.id, doc_type=DocType.TD)
    session.add(doc)
    await session.flush()
    fresh = Exercise(id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
                     number="1", number_normalized=1, statement="a",
                     hint_status=HintStatus.none)
    approved = Exercise(id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
                        number="2", number_normalized=2, statement="b",
                        hints=["done"], hint_status=HintStatus.approved)
    session.add_all([fresh, approved])
    await session.commit()
    return teacher, doc, fresh, approved


@pytest.mark.asyncio
async def test_generate_hints_batch_targets_only_none_exercises(db_session):
    teacher, doc, fresh, approved = await _seed_doc_with_exercises(db_session)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.post(f"/api/teacher/documents/{doc.id}/generate-hints")
    finally:
        await client.aclose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["queued"] == 1                       # only the `none` exercise
    assert body["job_id"] is not None

    await db_session.refresh(fresh)
    await db_session.refresh(approved)
    assert fresh.hint_status == HintStatus.generating   # claimed
    assert approved.hint_status == HintStatus.approved  # untouched (idempotent)

    job = (await db_session.execute(
        select(IngestionJob).where(IngestionJob.document_id == doc.id,
                                   IngestionJob.job_type == JobType.hint_generate)
    )).scalar_one()
    assert job.payload["exercise_ids"] == [str(fresh.id)]
    assert job.priority == 100                        # default queue, not urgent


@pytest.mark.asyncio
async def test_generate_hints_urgent_uses_priority_zero(db_session):
    teacher, doc, fresh, approved = await _seed_doc_with_exercises(db_session)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.post(f"/api/teacher/documents/{doc.id}/generate-hints",
                                 json={"urgent": True})
    finally:
        await client.aclose()

    assert resp.status_code == 200
    job = (await db_session.execute(
        select(IngestionJob).where(IngestionJob.job_type == JobType.hint_generate)
    )).scalar_one()
    assert job.priority == 0


@pytest.mark.asyncio
async def test_generate_hints_single_overrides_any_status(db_session):
    teacher, doc, fresh, approved = await _seed_doc_with_exercises(db_session)
    client = await make_client(db_session, teacher)
    try:
        # single-exercise entry is the only way to regenerate an approved one
        resp = await client.post(
            f"/api/teacher/documents/{doc.id}/exercises/{approved.id}/generate-hints"
        )
    finally:
        await client.aclose()

    assert resp.status_code == 200
    await db_session.refresh(approved)
    assert approved.hint_status == HintStatus.generating

    job = (await db_session.execute(
        select(IngestionJob).where(IngestionJob.job_type == JobType.hint_generate)
    )).scalar_one()
    assert job.payload["exercise_ids"] == [str(approved.id)]


@pytest.mark.asyncio
async def test_approve_transitions_pending_review_to_approved(db_session):
    teacher, doc, fresh, approved = await _seed_doc_with_exercises(db_session)
    fresh.hint_status = HintStatus.pending_review
    fresh.hints = ["draft nudge", "draft method", "draft close"]
    await db_session.commit()

    client = await make_client(db_session, teacher)
    try:
        resp = await client.put(
            f"/api/teacher/documents/{doc.id}/exercises/{fresh.id}/hints/approve"
        )
    finally:
        await client.aclose()

    assert resp.status_code == 200
    await db_session.refresh(fresh)
    assert fresh.hint_status == HintStatus.approved


@pytest.mark.asyncio
async def test_hand_editing_hints_marks_them_approved(db_session):
    """[v8.0 §10] A teacher hand-editing hints trusts their own text → approved
    directly (no self-review). Editing sets edited_by_teacher too (ingest keeps it)."""
    teacher, doc, fresh, approved = await _seed_doc_with_exercises(db_session)
    fresh.hint_status = HintStatus.pending_review
    await db_session.commit()

    client = await make_client(db_session, teacher)
    try:
        resp = await client.put(
            f"/api/teacher/documents/{doc.id}/exercises/{fresh.id}",
            json={"hints": ["my own hint"]},
        )
    finally:
        await client.aclose()

    assert resp.status_code == 200
    await db_session.refresh(fresh)
    assert fresh.hints == ["my own hint"]
    assert fresh.hint_status == HintStatus.approved


@pytest.mark.asyncio
async def test_generate_hints_rejects_foreign_document(db_session):
    teacher, doc, fresh, approved = await _seed_doc_with_exercises(db_session)
    intruder = make_user(role=UserRole.teacher)
    db_session.add(intruder)
    await db_session.commit()
    client = await make_client(db_session, intruder)
    try:
        resp = await client.post(f"/api/teacher/documents/{doc.id}/generate-hints")
    finally:
        await client.aclose()

    assert resp.status_code == 404
