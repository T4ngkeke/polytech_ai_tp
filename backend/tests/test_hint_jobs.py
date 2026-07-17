"""
test_hint_jobs.py — [v8.0 §10] the DB-aware hint-generation job runner.

`run_hint_job` fulfils a hint_generate job: it pairs answers to exercises (at
generation time, by number), then for each targeted exercise generates tiered
hints via the injected `generate_fn` and writes hints + hint_status +
hint_source. Exercises with no paired answer fail (blind-solve is deferred).
The generation model calls are inside `generate_fn`, so this runs on the DB
with a fake and no live model.
"""

import uuid

import pytest

from backend.app.models import (
    Answer, Audience, Class, Document, DocType, Exercise,
    HintSource, HintStatus, IngestionJob, JobType, Lab, UserRole,
)
from backend.worker.hints import HintResult
from backend.worker.hint_jobs import run_hint_job
from backend.tests.conftest import make_user


async def _seed_lab(session):
    teacher = make_user(role=UserRole.teacher)
    session.add(teacher)
    await session.flush()
    cls = Class(id=uuid.uuid4(), name="Algo", teacher_id=teacher.id,
                invite_code=uuid.uuid4().hex[:6])
    session.add(cls)
    await session.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name="Lab")
    session.add(lab)
    await session.flush()
    return teacher, cls, lab


def _doc(cls, lab, teacher, **kw):
    return Document(id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="d.pdf",
                    storage_path="/x", content_hash=uuid.uuid4().hex,
                    uploaded_by=teacher.id, **kw)


@pytest.mark.asyncio
async def test_run_hint_job_pairs_and_generates_for_targeted_exercises(db_session):
    teacher, cls, lab = await _seed_lab(db_session)
    td = _doc(cls, lab, teacher, doc_type=DocType.TD)
    corrige = _doc(cls, lab, teacher, doc_type=DocType.corrige)
    db_session.add_all([td, corrige])
    await db_session.flush()

    ex1 = Exercise(id=uuid.uuid4(), document_id=td.id, class_id=cls.id, lab_id=lab.id,
                   number="1", number_normalized=1, statement="Sum two numbers.",
                   hint_status=HintStatus.generating)
    ex2 = Exercise(id=uuid.uuid4(), document_id=td.id, class_id=cls.id, lab_id=lab.id,
                   number="2", number_normalized=2, statement="Sort a list.",
                   hint_status=HintStatus.generating)
    # Unpaired answers (pairing happens inside the runner, at generation time).
    db_session.add_all([
        ex1, ex2,
        Answer(id=uuid.uuid4(), document_id=corrige.id, number_raw="1",
               number_normalized=1, answer_text="42"),
        Answer(id=uuid.uuid4(), document_id=corrige.id, number_raw="2",
               number_normalized=2, answer_text="merge sort"),
    ])
    await db_session.commit()

    async def fake_generate(statement, answer_text):
        return HintResult(status=HintStatus.pending_review, hint_source=HintSource.worked,
                          hints=["nudge", "method", "close"])

    job = IngestionJob(id=uuid.uuid4(), document_id=td.id, job_type=JobType.hint_generate,
                       payload={"exercise_ids": [str(ex1.id), str(ex2.id)]})
    db_session.add(job)
    await db_session.flush()

    await run_hint_job(db_session, job, generate_fn=fake_generate)

    for ex in (ex1, ex2):
        await db_session.refresh(ex)
        assert ex.hints == ["nudge", "method", "close"]
        assert ex.hint_status == HintStatus.pending_review
        assert ex.hint_source == HintSource.worked

    # Answers were paired to their exercises as part of generation.
    a1 = (await db_session.execute(
        Answer.__table__.select().where(Answer.number_normalized == 1)
    )).first()
    assert a1.exercise_id == ex1.id


@pytest.mark.asyncio
async def test_run_hint_job_fails_exercise_with_no_answer(db_session):
    """No paired answer → failed (blind-solve from scratch is a deferred path)."""
    teacher, cls, lab = await _seed_lab(db_session)
    td = _doc(cls, lab, teacher, doc_type=DocType.TD)
    db_session.add(td)
    await db_session.flush()
    ex = Exercise(id=uuid.uuid4(), document_id=td.id, class_id=cls.id, lab_id=lab.id,
                  number="1", number_normalized=1, statement="Prove it.",
                  hint_status=HintStatus.generating)
    db_session.add(ex)
    await db_session.commit()

    async def fake_generate(statement, answer_text):  # must not be called
        raise AssertionError("generate_fn called for an answerless exercise")

    job = IngestionJob(id=uuid.uuid4(), document_id=td.id, job_type=JobType.hint_generate,
                       payload={"exercise_ids": [str(ex.id)]})
    db_session.add(job)
    await db_session.flush()

    await run_hint_job(db_session, job, generate_fn=fake_generate)

    await db_session.refresh(ex)
    assert ex.hint_status == HintStatus.failed
    assert ex.hints is None


async def _seed_paired_exercise(db_session, *, suspect: bool = False):
    """One TD exercise + one corrigé answer that pairs to it by number."""
    teacher, cls, lab = await _seed_lab(db_session)
    td = _doc(cls, lab, teacher, doc_type=DocType.TD)
    corrige = _doc(cls, lab, teacher, doc_type=DocType.corrige)
    db_session.add_all([td, corrige])
    await db_session.flush()
    ex = Exercise(id=uuid.uuid4(), document_id=td.id, class_id=cls.id, lab_id=lab.id,
                  number="1", number_normalized=1, statement="Sum two numbers.",
                  hint_status=HintStatus.generating)
    ans = Answer(id=uuid.uuid4(), document_id=corrige.id, number_raw="1",
                 number_normalized=1, answer_text="The capital of France is Paris.",
                 pairing_suspect=suspect)
    db_session.add_all([ex, ans])
    await db_session.commit()
    job = IngestionJob(id=uuid.uuid4(), document_id=td.id, job_type=JobType.hint_generate,
                       payload={"exercise_ids": [str(ex.id)]})
    db_session.add(job)
    await db_session.flush()
    return ex, ans, job


@pytest.mark.asyncio
async def test_run_hint_job_mismatch_skips_generation_and_flags_answer(db_session):
    """[v8.1] verify_fn says the answer does not answer the statement → no
    generation (failed), and the Answer row is flagged pairing_suspect."""
    ex, ans, job = await _seed_paired_exercise(db_session)

    async def fake_generate(statement, answer_text):  # must not be called
        raise AssertionError("generate_fn called despite a pairing mismatch")

    async def fake_verify(statement, answer_text):
        return False

    await run_hint_job(db_session, job, generate_fn=fake_generate, verify_fn=fake_verify)

    await db_session.refresh(ex)
    await db_session.refresh(ans)
    assert ex.hint_status == HintStatus.failed
    assert ex.hints is None
    assert ans.pairing_suspect is True


@pytest.mark.asyncio
async def test_run_hint_job_verify_pass_generates_and_clears_flag(db_session):
    """[v8.1] verify_fn approves the pairing → generation proceeds and a stale
    suspect flag (from an earlier mismatch) is cleared."""
    ex, ans, job = await _seed_paired_exercise(db_session, suspect=True)

    async def fake_generate(statement, answer_text):
        return HintResult(status=HintStatus.pending_review, hint_source=HintSource.worked,
                          hints=["nudge", "method", "close"])

    async def fake_verify(statement, answer_text):
        return True

    await run_hint_job(db_session, job, generate_fn=fake_generate, verify_fn=fake_verify)

    await db_session.refresh(ex)
    await db_session.refresh(ans)
    assert ex.hint_status == HintStatus.pending_review
    assert ex.hints == ["nudge", "method", "close"]
    assert ans.pairing_suspect is False
