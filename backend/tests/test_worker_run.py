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
    JobType,
    Lab,
    UserRole,
)
from backend.app.services.document_service import create_document
from backend.tests.conftest import make_user
from backend.worker.gpu_gate import ChatLoadGate
from backend.worker.main import make_fn_builder, run_tick


# --- [v8.0] config hot-reload: rebuild model clients only when config changes --
# The worker reads SystemConfig each loop iteration; an admin config edit takes
# effect without a restart, but the (heavier) client rebuild only runs on a change.

@pytest.mark.asyncio
async def test_fn_builder_rebuilds_only_when_config_changes():
    configs = [{"model": "a"}, {"model": "a"}, {"model": "b"}]
    i = {"n": 0}

    async def fake_load(_db):
        cfg = configs[i["n"]]
        i["n"] += 1
        return cfg

    builds: list[dict] = []

    def fake_build(cfg):
        builds.append(dict(cfg))
        return f"fns:{cfg['model']}"

    build = make_fn_builder(load_config=fake_load, build_fns=fake_build)

    assert await build(None) == "fns:a"   # first call → build
    assert await build(None) == "fns:a"   # unchanged config → reuse cached fns
    assert await build(None) == "fns:b"   # config changed → rebuild
    assert builds == [{"model": "a"}, {"model": "b"}]  # rebuilt only on change


class FakeGate:
    def __init__(self, is_open: bool):
        self._open = is_open
        self.calls = 0

    def should_run(self) -> bool:
        self.calls += 1
        return self._open


async def fake_embed(chunks):
    return [[0.0] * EMBEDDING_DIM for _ in chunks]


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
        pg_session, gate, embed_fn=fake_embed, sleep_fn=fake_sleep,
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
        pg_session, gate, embed_fn=fake_embed, sleep_fn=fake_sleep,
    )

    assert processed is True
    await pg_session.refresh(doc)
    assert doc.status == DocumentStatus.indexed
    assert sleeps == []  # did work, no idle sleep


@pytest.mark.asyncio
async def test_run_tick_forwards_run_hint_job(pg_session, tmp_path):
    """[v8.0 §10] run_tick threads run_hint_job through to process_one, so a
    hint_generate job actually runs in production (not just in a direct
    process_one call)."""
    doc = await _seed_job(pg_session, tmp_path)  # also an ingest job (priority 100)
    hint_job = IngestionJob(
        id=uuid.uuid4(), document_id=doc.id, job_type=JobType.hint_generate,
        payload={"exercise_ids": []}, priority=0,  # claimed ahead of ingest
    )
    pg_session.add(hint_job)
    await pg_session.commit()

    gate = FakeGate(is_open=True)
    seen = {}

    async def fake_run_hint_job(db, job):
        seen["job_id"] = job.id

    async def fake_sleep(seconds):
        pass

    processed = await run_tick(
        pg_session, gate, embed_fn=fake_embed, sleep_fn=fake_sleep,
        run_hint_job=fake_run_hint_job,
    )

    assert processed is True
    assert seen["job_id"] == hint_job.id


@pytest.mark.asyncio
async def test_run_tick_skips_when_chat_busy_even_if_gpu_idle(pg_session, tmp_path):
    """Primary gate: a busy chat pauses ingestion even when the GPU gate is open."""
    doc = await _seed_job(pg_session, tmp_path)
    gate = FakeGate(is_open=True)  # GPU idle
    chat_gate = ChatLoadGate(window=1)
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    async def busy_chat_load(_db):
        return 50.0  # lots of recent chat activity

    processed = await run_tick(
        pg_session, gate, embed_fn=fake_embed, sleep_fn=fake_sleep,
        chat_gate=chat_gate, chat_load_fn=busy_chat_load,
    )

    assert processed is False
    assert sleeps  # backed off
    job = (await pg_session.execute(
        IngestionJob.__table__.select().where(IngestionJob.document_id == doc.id)
    )).first()
    assert job.status == JobStatus.queued  # untouched


@pytest.mark.asyncio
async def test_run_tick_processes_when_chat_quiet_and_gpu_idle(pg_session, tmp_path):
    """Both gates open (chat quiet + GPU idle) → ingestion runs."""
    doc = await _seed_job(pg_session, tmp_path)
    gate = FakeGate(is_open=True)
    chat_gate = ChatLoadGate(window=1)
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    async def quiet_chat_load(_db):
        return 0.0  # no recent chat

    processed = await run_tick(
        pg_session, gate, embed_fn=fake_embed, sleep_fn=fake_sleep,
        chat_gate=chat_gate, chat_load_fn=quiet_chat_load,
    )

    assert processed is True
    await pg_session.refresh(doc)
    assert doc.status == DocumentStatus.indexed


@pytest.mark.asyncio
async def test_mark_job_is_none_safe(db_session):
    """[v7.2 fix] A job reclaimed/cascade-deleted during a long ingest must not
    crash the worker loop when its terminal status is written."""
    from backend.worker.main import _mark_job

    # No such job row — must be a no-op, not an AttributeError.
    await _mark_job(db_session, uuid.uuid4(), JobStatus.done)
