"""Reading progress of a PDF: pages read, the time left, the jobs ahead.

The worker writes how many pages it has read into the job row; the teacher's
document list turns it into "page 10 of 40, about 5 min left", or "1 document
ahead" while the job waits.
"""
import asyncio
import dataclasses
import uuid
from datetime import timedelta

import pytest

from backend.app.models import IngestionJob, JobStatus, _utcnow
from backend.app.services.document_service import reading_progress
from backend.tests import pdf_fixtures
from backend.tests.conftest import make_client
from backend.tests.test_structured_ingest import (  # noqa: F401  (fixtures)
    run_job, seed, storage, structured_on, upload,
)
from backend.worker import structured_ingest as si
from backend.worker.queue import claim_structured_job


def test_time_left_follows_the_pace_of_the_pages_read():
    now = _utcnow()
    started = (now - timedelta(seconds=100)).isoformat()
    assert reading_progress(None, now) is None
    assert reading_progress({'done': 0, 'total': None, 'started_at': started}, now) is None
    # Too few pages to guess.
    assert reading_progress({'done': 2, 'total': 40, 'started_at': started}, now) == {
        'done': 2, 'total': 40, 'seconds_left': None}
    # 10 pages in 100 s: 30 pages left take about 300 s.
    assert reading_progress({'done': 10, 'total': 40, 'started_at': started}, now) == {
        'done': 10, 'total': 40, 'seconds_left': 300}
    assert reading_progress({'done': 40, 'total': 40, 'started_at': started}, now)['seconds_left'] is None


@pytest.mark.asyncio
async def test_the_reader_reports_every_page_and_the_job_forgets_it(db_session, storage, tmp_path, monkeypatch):
    calls = []

    class Spy(si.PageProgress):
        def __call__(self, done, total):
            calls.append((done, total))
            super().__call__(done, total)

    monkeypatch.setattr(si, 'PageProgress', Spy)
    teacher, _, lab, _ = await seed(db_session)
    body = (await upload(db_session, teacher, lab, pdf_fixtures.course_pdf(3))).json()
    assert await run_job(db_session, storage, tmp_path)
    assert calls == [(0, 3), (1, 3), (2, 3), (3, 3)]
    job = await db_session.get(IngestionJob, uuid.UUID(body['job_id']))
    await db_session.refresh(job)
    assert job.task_kind == 'pdf_index' and job.payload is None


@pytest.mark.asyncio
async def test_only_the_claim_that_holds_the_job_writes_progress(db_session, storage):
    teacher, _, lab, _ = await seed(db_session)
    body = (await upload(db_session, teacher, lab, pdf_fixtures.course_pdf(3))).json()
    claim = await claim_structured_job(db_session, lease_seconds=600, now=_utcnow())
    job_id = uuid.UUID(body['job_id'])

    async def write(claim, done):
        progress, stop = si.PageProgress(), asyncio.Event()
        progress(done, 5)
        writer = asyncio.create_task(si._write_progress(db_session, claim, progress, stop, every=0.01))
        await asyncio.sleep(0.1)
        stop.set()
        await writer

    await write(claim, 2)
    job = await db_session.get(IngestionJob, job_id)
    await db_session.refresh(job)
    assert {k: job.payload['progress'][k] for k in ('stage', 'done', 'total')} == {
        'stage': 'reading', 'done': 2, 'total': 5}
    # A claim that lost the job (another lock token) writes nothing.
    await write(dataclasses.replace(claim, lock_token=uuid.uuid4()), 4)
    await db_session.refresh(job)
    assert job.payload['progress']['done'] == 2


@pytest.mark.asyncio
async def test_the_document_list_shows_jobs_ahead_then_pages_read(db_session, storage):
    teacher, _, lab, _ = await seed(db_session)
    first = (await upload(db_session, teacher, lab, pdf_fixtures.course_pdf(3))).json()
    second = (await upload(db_session, teacher, lab, pdf_fixtures.course_pdf(2, title='Autre'))).json()

    async def latest():
        client = await make_client(db_session, teacher)
        async with client:
            rows = (await client.get(f'/api/teacher/labs/{lab.id}/documents')).json()
        return {row['id']: row['latest_revision'] for row in rows}

    rows = await latest()
    assert rows[first['id']]['jobs_ahead'] == 0 and rows[second['id']]['jobs_ahead'] == 1

    claim = await claim_structured_job(db_session, lease_seconds=600, now=_utcnow())
    assert claim.job_id == uuid.UUID(first['job_id'])
    job = await db_session.get(IngestionJob, claim.job_id)
    job.payload = {'progress': {'stage': 'reading', 'done': 10, 'total': 40,
                                'started_at': (_utcnow() - timedelta(seconds=100)).isoformat()}}
    await db_session.commit()
    rows = await latest()
    progress = rows[first['id']]['progress']
    assert (progress['done'], progress['total']) == (10, 40)
    assert 290 <= progress['seconds_left'] <= 320
    assert rows[second['id']]['jobs_ahead'] == 1
    assert job.status == JobStatus.processing
