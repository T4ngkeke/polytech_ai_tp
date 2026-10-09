"""M4-A: rebuild four complete M3-C PDFs in isolated product tables.

MinerU page outputs and BGE-M3 vectors are the frozen M3-C artifacts. This
checks product upload/index/publication/search/read without model calls.
"""
import hashlib
import json
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from backend.app.agent.tools import ResearchTools, create_scope
from backend.app.config import settings
from backend.app.main import app
from backend.app.models import ClassStudent, DocChunk, Document, DocumentRevision, Session
from backend.app.routers.teacher import get_storage_root
from backend.app.services import document_versions
from backend.tests.conftest import make_client, make_user
from backend.tests.test_structured_ingest import seed
from backend.worker import structured_ingest as si
from backend.worker.pdf_reader import read_pdf
from backend.worker.structured_index import process_index_job

ROOT = Path(__file__).resolve().parents[2]
FROZEN = ROOT / 'documentation/ingestion-agent-acceptance/m3-pdf/2026-09-29-m3c'


def _hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _vectors():
    vectors = {}
    for line in (FROZEN / 'embedding-cache.jsonl').read_text().splitlines():
        row = json.loads(line)
        assert row['model'] == 'bge-m3:latest' and len(row['embedding']) == 1024
        vectors[row['text_sha256']] = row['embedding']
    return vectors


async def publish_frozen_pdf(db, teacher, lab_id, storage, tmp_path, monkeypatch, pdf: Path,
                             expected_sha256: str, page_count: int, content_lists: Path, embed):
    """Upload, parse from frozen MinerU pages, index and publish one PDF through
    the product APIs and workers. No MinerU or model call is made here."""
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert digest == expected_sha256
    await db.refresh(teacher)
    client = await make_client(db, teacher)
    async with client:
        response = await client.post(f'/api/teacher/labs/{lab_id}/documents',
            files={'file': (pdf.name, pdf.read_bytes(), 'application/pdf')})
    assert response.status_code == 201, response.text
    upload = response.json()
    cache = content_lists / pdf.stem

    def cached_page(_source, page_no, _work_dir):
        page = json.loads((cache / f'page-{page_no}.json').read_text())
        assert page['pdf_sha256'] == digest and page['page_no'] == page_no
        return page['content_list']

    def cached_pipeline(source, source_digest, work_dir, *, settings):
        parsed, metrics = read_pdf(source, source_digest, work_dir, cached_page)
        return si.ParseOutcome(parsed, {'status': 'source_pdf', 'extractor': metrics},
            {'kind': 'same_pdf_physical_page', 'pages': page_count}, True)

    monkeypatch.setattr(si, 'run_pdf_pipeline', cached_pipeline)
    worker = si.StructuredSettings(storage_root=storage, task_root=tmp_path / 'tasks')
    assert await si.process_structured_job(db, settings=worker)
    assert await process_index_job(db, embed_fn=embed)
    doc = await db.get(Document, uuid.UUID(upload['id']), populate_existing=True)
    rev = await db.get(DocumentRevision, uuid.UUID(upload['revision_id']), populate_existing=True)
    assert rev.index_ready
    await db.refresh(teacher)
    await document_versions.publish_revision(db, document_id=doc.id,
        revision_id=rev.id, teacher_id=teacher.id,
        expected_generation=doc.generation, acknowledge_issues=True)
    await db.commit()


@pytest.mark.asyncio
async def test_four_pdfs_rebuild_and_tool_sources(pg_session, tmp_path, monkeypatch):
    if not FROZEN.exists():
        pytest.skip('frozen M3-C corpus (local documentation files) not present')
    db = pg_session
    monkeypatch.setattr(settings, 'STRUCTURED_INGEST_ENABLED', True)
    monkeypatch.setattr(settings, 'PDF_STRUCTURED_INGEST_ENABLED', True)
    storage = tmp_path / 'storage'
    storage.mkdir()
    app.dependency_overrides[get_storage_root] = lambda: str(storage)
    try:
        mapping = json.loads((FROZEN / 'question-map-reviewed.json').read_text())
        evidence = {e['pdf_path']: e for q in mapping['questions'] for e in q['required_evidence']}
        vectors = _vectors()
        teacher, _, lab, _ = await seed(db)
        await db.refresh(teacher)
        await db.refresh(lab)
        teacher_id, lab_id, class_id = teacher.id, lab.id, lab.class_id
        async def cached_embed(texts):
            return [vectors[_hash(text)] for text in texts]
        for relative, meta in sorted(evidence.items()):
            await publish_frozen_pdf(db, teacher, lab_id, storage, tmp_path, monkeypatch,
                ROOT / relative, meta['pdf_sha256'], meta['pdf_page_count'],
                FROZEN / 'content-list', cached_embed)
        count = await db.scalar(select(func.count(DocChunk.id)))
        assert count == 631
        student = make_user()
        db.add(student)
        await db.flush()
        student_id = student.id
        db.add(ClassStudent(class_id=class_id, student_id=student_id))
        chat = Session(id=uuid.uuid4(), lab_id=lab_id, user_id=student_id)
        db.add(chat)
        chat_id = chat.id
        await db.commit()
        scope = await create_scope(db, user_id=student_id, session_id=chat_id)
        assert len(scope.snapshot_revisions) == 4
        async def query_embed(text):
            return vectors[_hash(text)]
        tools = ResearchTools(db, scope, embed_fn=query_embed)
        by_doc = {doc.id: doc.filename for doc in (await db.scalars(select(Document))).all()}
        outcomes = []
        for q in mapping['questions']:
            result = await tools.execute('search', {'query': q['question'], 'top_k': 6})
            assert result['ok'] and 'embedding_unavailable' not in result['issues']
            assert 'source_identity_conflict' not in result['issues']
            pages = {(by_doc[uuid.UUID(item['document_id'])], item['pdf_page_no'])
                     for item in result['items']}
            gold = {(Path(e['pdf_path']).name, e['pdf_page_no']) for e in q['required_evidence']}
            outcomes.append({'question_id': q['question_id'],
                             'pages': sorted([list(p) for p in pages]),
                             'any_gold': bool(pages & gold), 'all_gold': gold <= pages})
            if q['question_id'] == 'Q10':
                target = next(item for item in result['items'] if
                    by_doc[uuid.UUID(item['document_id'])] == 'De_LAlgebre_Relationnel_au_SQL_2022.pdf'
                    and item['pdf_page_no'] == 9)
                snippet = target['snippets'][0]
                excerpt = await tools.execute('read', {
                    'document_id': target['document_id'], 'block_id': target['block_id'],
                    'start': snippet['range']['start'], 'end': snippet['range']['end'],
                })
                assert excerpt['ok'] and excerpt['items'][0]['content'] == snippet['content']
                assert excerpt['items'][0]['page']['preview_page_no'] == 9
        summary = {
            'pdf_count': len(scope.snapshot_revisions), 'chunk_count': count,
            'questions': outcomes,
            'any_gold': sum(x['any_gold'] for x in outcomes),
            'all_gold': sum(x['all_gold'] for x in outcomes),
            'model_calls': 0, 'embedding_source': 'frozen BGE-M3 cache',
        }
        output = os.getenv('M4A_RESULTS_OUT')
        if output:
            Path(output).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        assert next(x for x in outcomes if x['question_id'] == 'Q10')['any_gold']
    finally:
        app.dependency_overrides.pop(get_storage_root, None)


CORPUS_V3 = ROOT / 'documentation/ingestion-agent-acceptance/m3-pdf/2026-10-05-corpus-v3'


async def build_corpus_v3(db, tmp_path, monkeypatch, *, embed_url: str | None = None) -> dict:
    """The 14-PDF corpus: the four M3-C PDFs plus ten more, all mineru-text.

    Chunk vectors come from the frozen M3-C cache and the corpus-v3 cache. A
    missing vector is computed once with the same BGE-M3 service and appended
    to the v3 cache; later runs read only from the caches.
    """
    import httpx
    monkeypatch.setattr(settings, 'STRUCTURED_INGEST_ENABLED', True)
    monkeypatch.setattr(settings, 'PDF_STRUCTURED_INGEST_ENABLED', True)
    storage = tmp_path / 'storage'
    storage.mkdir()
    app.dependency_overrides[get_storage_root] = lambda: str(storage)
    corpus = json.loads((CORPUS_V3 / 'corpus.json').read_text())
    vectors = _vectors()
    cache_file = CORPUS_V3 / 'embedding-cache.jsonl'
    if cache_file.exists():
        vectors.update({row['text_sha256']: row['embedding']
                        for row in map(json.loads, cache_file.read_text().splitlines())})
    added = 0

    async def embed(texts):
        nonlocal added
        missing = list(dict.fromkeys(t for t in texts if _hash(t) not in vectors))
        if missing:
            if not embed_url:
                raise AssertionError(f'{len(missing)} chunk vectors are not frozen; pass embed_url once')
            async with httpx.AsyncClient(timeout=120) as client:
                for start in range(0, len(missing), 64):
                    batch = missing[start:start + 64]
                    response = await client.post(embed_url, json={'model': 'bge-m3:latest', 'input': batch})
                    response.raise_for_status()
                    with cache_file.open('a') as stream:
                        for text, vector in zip(batch, response.json()['embeddings']):
                            assert len(vector) == 1024
                            vectors[_hash(text)] = vector
                            stream.write(json.dumps({'text_sha256': _hash(text), 'model': 'bge-m3:latest',
                                                     'embedding': vector}) + '\n')
                            added += 1
        return [vectors[_hash(text)] for text in texts]

    try:
        teacher, _, lab, _ = await seed(db)
        await db.refresh(teacher)
        await db.refresh(lab)
        lab_id, class_id = lab.id, lab.class_id
        for entry in corpus['documents']:
            await publish_frozen_pdf(db, teacher, lab_id, storage, tmp_path, monkeypatch,
                ROOT / entry['pdf'], entry['sha256'], entry['page_count'],
                ROOT / entry['content_lists'], embed)
        student = make_user()
        db.add(student)
        await db.flush()
        db.add(ClassStudent(class_id=class_id, student_id=student.id))
        db.add(Session(id=uuid.uuid4(), lab_id=lab_id, user_id=student.id))
        await db.commit()
        return {'documents': len(corpus['documents']),
                'chunks': await db.scalar(select(func.count(DocChunk.id))),
                'new_vectors': added}
    finally:
        app.dependency_overrides.pop(get_storage_root, None)
