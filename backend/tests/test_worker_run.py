"""
test_worker_run.py — v7 worker loop decision logic (one tick).

`run_tick` gates on the GPU gate, processes at most one job, and sleeps when
idle/closed. The infinite `run_forever` loop is thin glue over this.
"""

import uuid

import pytest

from backend.app.models import (
    Class,
    Document,
    DocumentStatus,
    EMBEDDING_DIM,
    IngestionJob,
    JobStatus,
    Lab,
    UserRole,
)
from backend.app.services.document_service import create_document
from backend.tests.conftest import make_user
from backend.worker.main import run_tick


class FakeGate:
    def __init__(self, is_open: bool):
        self._open = is_open
        self.calls = 0

    def should_run(self) -> bool:
        self.calls += 1
        return self._open


async def fake_embed(chunks):
    return [[0.0] * EMBEDDING_DIM for _ in chunks]


async def fake_extract(text):
    return [{"number": "Ex 1", "statement": "do it", "hints": None,
             "concept": None}]


async def _seed_job(session, tmp_path):
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
    return await create_document(
        session, class_id=cls.id, lab_id=lab.id, filename="d.txt",
        content=b"para one\n\npara two", uploaded_by=teacher.id, storage_root=tmp_path,
    )


@pytest.mark.asyncio
async def test_run_tick_skips_and_sleeps_when_gate_closed(pg_session, tmp_path):
    doc = await _seed_job(pg_session, tmp_path)
    gate = FakeGate(is_open=False)
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    processed = await run_tick(
        pg_session, gate, embed_fn=fake_embed, extract_fn=fake_extract, sleep_fn=fake_sleep,
    )

    assert processed is False
    assert sleeps  # it slept instead of working
    job = (await pg_session.execute(
        IngestionJob.__table__.select().where(IngestionJob.document_id == doc.id)
    )).first()
    assert job.status == JobStatus.queued  # untouched


@pytest.mark.asyncio
async def test_run_tick_processes_when_gate_open(pg_session, tmp_path):
    doc = await _seed_job(pg_session, tmp_path)
    gate = FakeGate(is_open=True)
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    processed = await run_tick(
        pg_session, gate, embed_fn=fake_embed, extract_fn=fake_extract, sleep_fn=fake_sleep,
    )

    assert processed is True
    await pg_session.refresh(doc)
    assert doc.status == DocumentStatus.indexed
    assert sleeps == []  # did work, no idle sleep
