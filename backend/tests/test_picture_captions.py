"""Picture captions at ingest: which pictures, cache, failures, time budget, and
the parse job that stores them as unchecked descriptions."""
import asyncio
import hashlib
import uuid

import pymupdf
import pytest
from sqlalchemy import select

from backend.app.config import settings
from backend.app.main import app
from backend.app.models import DocChunk, DocumentBlock, DocumentRevision, PictureCaption
from backend.app.routers.teacher import get_storage_root
from backend.app.services import document_edits, document_versions
from backend.app.services.model_routing import Endpoint
from backend.tests.conftest import make_client
from backend.tests.test_structured_ingest import seed
from backend.worker import picture_captions as pc
from backend.worker import structured_ingest as si
from backend.worker.pdf_reader import read_pdf
from backend.worker.structured_index import process_index_job


def deck(colors):
    """One page per colour: a title and a coloured square (the picture)."""
    pdf = pymupdf.open()
    for color in colors:
        page = pdf.new_page(width=842, height=595)
        page.insert_text((60, 60), 'Titre de la diapositive', fontsize=24)
        page.draw_rect(pymupdf.Rect(300, 150, 600, 450), color=color, fill=color)
    data = pdf.tobytes()
    pdf.close()
    return data


def items_with(bbox):
    def items(_path, _number, _output):
        return [{'type': 'text', 'text': 'Titre de la diapositive', 'text_level': 1,
                 'bbox': [60, 40, 700, 120], 'page_idx': 0},
                {'type': 'image', 'sub_type': 'flowchart', 'content': 'graph TD A --> B',
                 'bbox': bbox, 'page_idx': 0}]
    return items


def read(tmp_path, colors, bbox=(356, 252, 713, 756)):
    tmp_path.mkdir(exist_ok=True)
    path = tmp_path / 'deck.pdf'
    path.write_bytes(deck(colors))
    doc, _ = read_pdf(path, hashlib.sha256(path.read_bytes()).hexdigest(), tmp_path / 'work',
                      items_with(list(bbox)))
    return doc, path


def test_pictures_are_cropped_and_repeats_and_icons_are_left_out(tmp_path):
    red, blue = (1, 0, 0), (0, 0, 1)
    doc, path = read(tmp_path, [red, red, red, blue])
    pictures = pc.find_pictures(doc, path)
    assert [p.skip for p in pictures] == ['repeated', 'repeated', 'repeated', None]
    assert pictures[3].png.startswith(b'\x89PNG') and len(pictures[3].image_sha256) == 64
    assert pictures[3].title == 'Titre de la diapositive'
    doc, path = read(tmp_path / 'icon', [blue], bbox=(400, 400, 420, 430))
    assert [p.skip for p in pc.find_pictures(doc, path)] == ['small']


def picture(digest, skip=None):
    block = type('Block', (), {'caption': None, 'caption_source': None})()
    return pc.Picture(block, 'Titre', png=b'png' + digest.encode(), image_sha256=digest, skip=skip)


@pytest.mark.asyncio
async def test_cache_failures_and_the_time_budget():
    calls = []

    async def describe(png, title):
        calls.append(png)
        if png == b'pngbad':
            raise ValueError('model down')
        if png == b'pngslow':
            await asyncio.sleep(5)
        return 'Un schéma ' + png.decode()

    pictures = [picture('new'), picture('new'), picture('old'), picture('bad'), picture('slow'),
                picture('logo', skip='repeated')]
    report, written = await pc.write_captions(pictures, model='m', describe=describe,
                                              cached={'old': 'Déjà écrit'}, budget_seconds=0.5)
    assert written == {'new': 'Un schéma pngnew'}
    assert sorted(calls) == [b'pngbad', b'pngnew', b'pngslow']      # one call per picture hash
    assert [p.block.caption for p in pictures] == ['Un schéma pngnew', 'Un schéma pngnew', 'Déjà écrit',
                                                   None, None, None]
    assert pictures[2].block.caption_source['cached'] is True
    assert {k: report[k] for k in ('written', 'cached', 'failed', 'not_reached', 'skipped_repeated')} == \
        {'written': 2, 'cached': 1, 'failed': 1, 'not_reached': 1, 'skipped_repeated': 1}
    assert report['error'] == 'ValueError'


@pytest.mark.asyncio
async def test_the_parse_job_stores_captions_as_unchecked_descriptions(db_session, tmp_path, monkeypatch):
    await parse_job_with_captions(db_session, tmp_path, monkeypatch)


@pytest.mark.asyncio
async def test_the_caption_cache_on_postgresql(pg_session, tmp_path, monkeypatch):
    await parse_job_with_captions(pg_session, tmp_path, monkeypatch)


async def parse_job_with_captions(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'STRUCTURED_INGEST_ENABLED', True)
    monkeypatch.setattr(settings, 'PDF_STRUCTURED_INGEST_ENABLED', True)
    storage = tmp_path / 'storage'
    storage.mkdir()
    app.dependency_overrides[get_storage_root] = lambda: str(storage)
    calls = []

    async def describe(png, title):
        calls.append(title)
        return 'Un carré bleu sur fond blanc.'
    monkeypatch.setattr(pc, 'make_describe', lambda _settings: describe)
    worker = si.StructuredSettings(storage_root=storage, task_root=tmp_path / 'tasks',
                                   captions=pc.CaptionSettings(Endpoint('http://model', 'k', 'flash-next')))
    try:
        teacher, _, lab, _ = await seed(db)
        lab_id = lab.id
        pdf = deck([(0, 0, 1)])

        async def upload_and_parse():
            await db.refresh(teacher)
            client = await make_client(db, teacher)
            async with client:
                response = await client.post(f'/api/teacher/labs/{lab_id}/documents',
                                             files={'file': ('cours.pdf', pdf, 'application/pdf')})
            assert response.status_code == 201, response.text

            def pipeline(source, digest, work_dir, *, settings, extractor=None, on_page=None):
                doc, metrics = read_pdf(source, digest, work_dir, items_with([356, 252, 713, 756]))
                return si.ParseOutcome(doc, {'status': 'source_pdf', 'extractor': metrics},
                                       {'kind': 'same_pdf_physical_page', 'pages': 1}, True)
            monkeypatch.setattr(si, 'run_pdf_pipeline', pipeline)
            assert await si.process_structured_job(db, settings=worker)
            return uuid.UUID(response.json()['revision_id'])

        rev_id = await upload_and_parse()
        figure = (await db.scalars(select(DocumentBlock).where(
            DocumentBlock.revision_id == rev_id, DocumentBlock.kind == 'figure'))).one()
        assert figure.content_description == 'Un carré bleu sur fond blanc.'
        assert figure.description_origin == 'model' and not document_edits.description_checked(figure)
        assert figure.structure['caption']['prompt_version'] == pc.PROMPT_VERSION
        rev = await db.get(DocumentRevision, rev_id, populate_existing=True)
        report = rev.report['render']['captions']
        assert (report['written'], report['cached'], report['failed']) == (1, 0, 0)
        assert calls == ['Titre de la diapositive']
        assert len((await db.scalars(select(PictureCaption))).all()) == 1

        async def embed(texts):
            return [[0.0] * 1023 + [1.0] for _ in texts]
        assert await process_index_job(db, embed_fn=embed)
        chunks = (await db.scalars(select(DocChunk).where(DocChunk.revision_id == rev_id))).all()
        assert [c.content for c in chunks if document_versions.chunk_of_description(c.source_ref)] == \
            ['Un carré bleu sur fond blanc.']

        # The same picture in a new upload comes from the cache.
        rev_id = await upload_and_parse()
        rev = await db.get(DocumentRevision, rev_id, populate_existing=True)
        assert rev.report['render']['captions']['cached'] == 1 and len(calls) == 1
    finally:
        app.dependency_overrides.pop(get_storage_root, None)


@pytest.mark.asyncio
async def test_a_model_error_never_fails_the_parse_job(db_session, tmp_path, monkeypatch):
    db = db_session
    monkeypatch.setattr(settings, 'STRUCTURED_INGEST_ENABLED', True)
    monkeypatch.setattr(settings, 'PDF_STRUCTURED_INGEST_ENABLED', True)
    storage = tmp_path / 'storage'
    storage.mkdir()
    app.dependency_overrides[get_storage_root] = lambda: str(storage)

    def broken(_doc, _path):
        raise RuntimeError('render failed')
    monkeypatch.setattr(pc, 'find_pictures', broken)
    worker = si.StructuredSettings(storage_root=storage, task_root=tmp_path / 'tasks',
                                   captions=pc.CaptionSettings(Endpoint('http://model', 'k', 'flash-next')))
    try:
        teacher, _, lab, _ = await seed(db)
        client = await make_client(db, teacher)
        async with client:
            response = await client.post(f'/api/teacher/labs/{lab.id}/documents',
                                         files={'file': ('cours.pdf', deck([(0, 0, 1)]), 'application/pdf')})

        def pipeline(source, digest, work_dir, *, settings, extractor=None, on_page=None):
            doc, metrics = read_pdf(source, digest, work_dir, items_with([356, 252, 713, 756]))
            return si.ParseOutcome(doc, {'status': 'source_pdf', 'extractor': metrics},
                                   {'kind': 'same_pdf_physical_page', 'pages': 1}, True)
        monkeypatch.setattr(si, 'run_pdf_pipeline', pipeline)
        assert await si.process_structured_job(db, settings=worker)
        rev = await db.get(DocumentRevision, uuid.UUID(response.json()['revision_id']), populate_existing=True)
        assert rev.processing_state == 'parsed'
        assert rev.report['render']['captions'] == {'error': 'RuntimeError'}
    finally:
        app.dependency_overrides.pop(get_storage_root, None)


@pytest.mark.asyncio
async def test_the_backfill_asks_one_reading_per_document_without_captions(db_session, tmp_path, monkeypatch):
    from backend.app.models import Document, IngestionJob, SystemConfig
    from backend.tests.test_structured_index_m3b import _parsed_pdf
    from scripts import caption_backfill
    db = db_session
    try:
        doc, rev, teacher, job = await _parsed_pdf(db, tmp_path, monkeypatch)

        async def embed(texts):
            return [[0.0] * 1023 + [1.0] for _ in texts]
        assert await process_index_job(db, embed_fn=embed)       # published, no captions
        doc_id = doc.id
        assert not await caption_backfill.captions_on(db)
        db.add(SystemConfig(key='PICTURE_CAPTIONS', value='on'))
        await db.commit()
        assert await caption_backfill.captions_on(db)
        todo = await caption_backfill.plan(db)
        assert [d.id for d, _, _ in todo] == [doc_id]
        await caption_backfill.ask_reading(db, *todo[0])
        jobs = (await db.scalars(select(IngestionJob).where(IngestionJob.document_id == doc_id,
                                                            IngestionJob.task_kind == 'pdf_parse'))).all()
        assert sum(j.status.value == 'queued' for j in jobs) == 1
        assert await caption_backfill.plan(db) == []             # a reading is already waiting
        assert (await db.get(Document, doc_id)).lifecycle == 'active'
    finally:
        app.dependency_overrides.pop(get_storage_root, None)
