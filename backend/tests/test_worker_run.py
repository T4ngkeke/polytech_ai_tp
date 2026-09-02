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
async def test_run_tick_urgent_job_skips_chat_busy_gate(pg_session, tmp_path):
    """An urgent job (priority=0) is processed NOW even while students are
    chatting — it bypasses the chat-load gate, so a teacher who needs a document
    re-indexed before class isn't blocked waiting for a quiet window."""
    doc = await _seed_job(pg_session, tmp_path)  # ingest job, priority 100
    urgent_doc = await _seed_job(pg_session, tmp_path)
    urgent = (await pg_session.execute(
        IngestionJob.__table__.select().where(IngestionJob.document_id == urgent_doc.id)
    )).first()
    await pg_session.execute(
        IngestionJob.__table__.update()
        .where(IngestionJob.id == urgent.id).values(priority=0)
    )
    await pg_session.commit()

    gate = FakeGate(is_open=True)  # GPU idle
    chat_gate = ChatLoadGate(window=1)
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    async def busy_chat_load(_db):
        return 50.0  # class is actively chatting → chat gate would normally close

    processed = await run_tick(
        pg_session, gate, embed_fn=fake_embed, sleep_fn=fake_sleep,
        chat_gate=chat_gate, chat_load_fn=busy_chat_load,
    )

    assert processed is True             # urgent bypassed the busy-chat gate
    await pg_session.refresh(urgent_doc)
    assert urgent_doc.status == DocumentStatus.indexed   # the priority-0 job ran first
    assert sleeps == []                  # did work, no back-off sleep


@pytest.mark.asyncio
async def test_run_tick_urgent_job_skips_gpu_gate(pg_session, tmp_path):
    """"Do it now" must run immediately even when the GPU is busy serving chat:
    an urgent job (priority=0) bypasses the GPU idle gate too, not just the
    chat-load gate — whoever asked already accepted competing for compute.
    Without this, "now" would silently wait for a GPU-idle window."""
    doc = await _seed_job(pg_session, tmp_path)  # ingest job, priority 100
    urgent_doc = await _seed_job(pg_session, tmp_path)
    urgent = (await pg_session.execute(
        IngestionJob.__table__.select().where(IngestionJob.document_id == urgent_doc.id)
    )).first()
    await pg_session.execute(
        IngestionJob.__table__.update()
        .where(IngestionJob.id == urgent.id).values(priority=0)
    )
    await pg_session.commit()

    gate = FakeGate(is_open=False)  # GPU busy → would normally close the gate
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    processed = await run_tick(
        pg_session, gate, embed_fn=fake_embed, sleep_fn=fake_sleep,
    )

    assert processed is True             # urgent bypassed the GPU idle gate
    await pg_session.refresh(urgent_doc)
    assert urgent_doc.status == DocumentStatus.indexed   # the priority-0 job ran
    assert sleeps == []                  # ran now, no back-off sleep


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
