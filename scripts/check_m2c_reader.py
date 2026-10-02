"""M2-C real-file check: read, grep, edit and reparse parsed ODP drafts.

Not content acceptance. For each ODP of the corpus, on a random schema of a
disposable *_test database and a new storage root: upload and parse it (real
renderer compose), read every page through the teacher HTTP endpoint, read
every block in small slices and rebuild it, grep one native line per page,
check a student cannot read the draft, edit one block, reparse the same
source with the real renderer, and check the edit was carried forward.

  .venv/bin/python scripts/check_m2c_reader.py \
      --database-url postgresql+asyncpg://...:.../name_test --output-root /tmp/<new dir>
"""
import argparse
import asyncio
import json
import re
import sys
import time
import uuid
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from backend.app.auth import create_access_token  # noqa: E402
from backend.app.config import settings  # noqa: E402
from backend.app.database import Base, get_db  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.models import (  # noqa: E402
    Class, ClassStudent, Document, DocumentBlock, DocumentPage, DocumentRevision, Lab, Session, User, UserRole,
)
from backend.app.routers.teacher import get_storage_root  # noqa: E402
from backend.app.services import document_reader as reader  # noqa: E402
from backend.app.services import structured_upload  # noqa: E402
from backend.worker import structured_ingest as si  # noqa: E402
from backend.worker.slide_renderer import RenderSettings  # noqa: E402
from scripts.check_m2b_structured import compose_runner, notes_lines  # noqa: E402
from scripts.migrate_document_snapshots import apply_migration  # noqa: E402


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--corpus', type=Path, default=REPOSITORY / 'cours/odp_files')
    args = parser.parse_args()
    if not make_url(args.database_url).database.endswith('_test'):
        raise SystemExit('refusing: the database name must end with _test')
    root = args.output_root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    storage, tasks, logs = root / 'storage', root / 'tasks', root / 'renderer-logs'
    for folder in (storage, tasks, logs):
        folder.mkdir()
    settings.STRUCTURED_INGEST_ENABLED = True  # this process only
    schema = 'm2c_real_' + uuid.uuid4().hex[:12]
    admin = create_async_engine(args.database_url, poolclass=NullPool)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_async_engine(args.database_url, poolclass=NullPool,
                                 connect_args={'server_settings': {'search_path': f'{schema},public'}})
    worker = si.StructuredSettings(storage_root=storage, task_root=tasks,
                                   render=RenderSettings(want_page_images=True, timeout_seconds=660))
    rows = []
    try:
        async with engine.begin() as conn:
            await conn.execute(text('CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public'))
            await conn.run_sync(Base.metadata.create_all)
            await apply_migration(conn)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session
        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_storage_root] = lambda: str(storage)
        async with factory() as db:
            teacher = User(id=uuid.uuid4(), username='m2c-check', hashed_password='x', role=UserRole.teacher)
            student = User(id=uuid.uuid4(), username='m2c-student', hashed_password='x', role=UserRole.student)
            db.add_all([teacher, student])
            await db.flush()
            cls = Class(id=uuid.uuid4(), name='M2-C check', teacher_id=teacher.id, invite_code='m2cchk')
            db.add(cls)
            await db.flush()
            lab = Lab(id=uuid.uuid4(), class_id=cls.id, name='TP')
            db.add(lab)
            await db.flush()
            db.add(ClassStudent(class_id=cls.id, student_id=student.id))
            chat = Session(id=uuid.uuid4(), lab_id=lab.id, user_id=student.id)
            db.add(chat)
            await db.commit()
            teacher_id, lab_id, chat_id = teacher.id, lab.id, chat.id
        auth = lambda user_id: {'Authorization': f'Bearer {create_access_token(user_id=user_id)}'}  # noqa: E731
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url='http://check') as http:
            for source in sorted(args.corpus.glob('*.odp')):
                row = {'file': source.name}
                started = time.monotonic()
                async with factory() as db:
                    lab = await db.get(Lab, lab_id)
                    up = await structured_upload.upload_odp(
                        db, teacher_id=teacher_id, lab=lab, shared=False, filename=source.name,
                        content=source.read_bytes(), language='fr', storage_root=storage)
                    doc_id, rev_id = up.document.id, up.revision.id
                    assert await si.process_structured_job(db, settings=worker, runner=compose_runner(logs))
                    rev = await db.get(DocumentRevision, rev_id)
                    await db.refresh(rev)
                    row['parse_state'] = rev.processing_state
                    pages = (await db.scalars(select(DocumentPage).where(DocumentPage.revision_id == rev_id)
                                              .order_by(DocumentPage.order_index))).all()
                    blocks = (await db.scalars(select(DocumentBlock).where(DocumentBlock.revision_id == rev_id))).all()
                    page_ids = [p.page_id for p in pages]
                base = f'/api/teacher/documents/{doc_id}/revisions/{rev_id}'
                # 1. Every page through the HTTP endpoint.
                seen_blocks, page_errors, leaks = 0, 0, 0
                bodies = []
                for page_id in page_ids:
                    resp = await http.get(f'{base}/pages/{page_id}', headers=auth(teacher_id))
                    if resp.status_code != 200:
                        page_errors += 1
                        continue
                    bodies.append(resp.text)
                    seen_blocks += len(resp.json()['blocks'])
                joined = '\n'.join(bodies)
                leaks += int(str(storage) in joined) + len([n for n in notes_lines(source) if n in joined])
                # 2. Every block read in 500-character slices and rebuilt.
                rebuilt_ok, slices = 0, 0
                async with factory() as db:
                    rev = await db.get(DocumentRevision, rev_id)
                    for block in blocks:
                        parts, cursor = [], None
                        while True:
                            out = await reader.read(db, rev, reader='check', teacher=True, block_id=block.block_id,
                                                    budget=500, cursor=cursor)
                            parts.append(out['items'][0]['content'])
                            slices += 1
                            cursor = out['next_cursor']
                            if not cursor:
                                break
                        rebuilt_ok += ''.join(parts) == block.content
                    # 3. grep one native line of each page and check the ranges.
                    grep_pages, grep_hits, grep_exact = 0, 0, 0
                    by_page = {}
                    for block in blocks:
                        by_page.setdefault(block.page_id, []).append(block)
                    for page_id in page_ids:
                        line = next((ln.strip() for b in sorted(by_page.get(page_id, []), key=lambda b: b.order_index)
                                     for ln in (b.content or '').splitlines() if len(ln.strip()) >= 12), None)
                        if not line:
                            continue
                        grep_pages += 1
                        result = await reader.grep(db, rev, reader='check', pattern=line[:200])
                        content_of = {str(b.block_id): b.content for b in blocks}
                        hits = [i for i in result['items'] if i['page']['page_id'] == str(page_id)]
                        grep_hits += bool(hits)
                        grep_exact += all(
                            content_of[i['block_id']][i['range']['start']:i['range']['end']].lower() == line[:200].lower()
                            for i in result['items'])
                # 4. A student never reads the draft.
                student_resp = await http.get(f'/api/documents/{doc_id}/revisions/{rev_id}/pages/{page_ids[0]}',
                                              params={'session_id': str(chat_id)}, headers=auth(student.id))
                # 5. Edit one block, then reparse the same source for real.
                target = next(b for b in sorted(blocks, key=lambda b: (page_ids.index(b.page_id), b.order_index))
                              if b.kind in ('text', 'code') and (b.content or '').strip())
                generation = (await http.get(f'/api/teacher/documents/{doc_id}/revisions',
                                             headers=auth(teacher_id))).json()['generation']
                edited_text = target.content + '\n[corrige par le professeur]'
                edit = await http.put(f'/api/teacher/documents/{doc_id}/blocks/{target.block_id}',
                                      headers=auth(teacher_id), json={'expected_revision_id': str(rev_id),
                                      'expected_generation': generation, 'content': edited_text})
                edit_rev = edit.json()['revision_id']
                reparse = await http.post(f'/api/teacher/documents/{doc_id}/revisions/{edit_rev}/reprocess',
                                          headers=auth(teacher_id), json={'expected_generation': edit.json()['generation']})
                reparse_rev = uuid.UUID(reparse.json()['revision_id'])
                async with factory() as db:
                    assert await si.process_structured_job(db, settings=worker, runner=compose_runner(logs))
                    rev2 = await db.get(DocumentRevision, reparse_rev)
                    carried_block = await db.scalar(select(DocumentBlock).where(
                        DocumentBlock.revision_id == reparse_rev, DocumentBlock.block_id == target.block_id))
                    original = await db.scalar(select(DocumentBlock.content).where(
                        DocumentBlock.revision_id == rev_id, DocumentBlock.block_id == target.block_id))
                    same_blocks = set((await db.scalars(select(DocumentBlock.block_id).where(
                        DocumentBlock.revision_id == reparse_rev))).all()) == {b.block_id for b in blocks}
                    doc = await db.get(Document, doc_id)
                row.update(
                    pages=len(page_ids), page_http_errors=page_errors, blocks=len(blocks), blocks_seen_http=seen_blocks,
                    storage_or_notes_leaks=leaks, blocks_rebuilt_from_slices=rebuilt_ok, read_slices=slices,
                    grep_pages=grep_pages, grep_found_on_its_page=grep_hits, grep_ranges_exact=grep_exact,
                    student_draft_status=student_resp.status_code, edit_status=edit.status_code,
                    edit_state=edit.json().get('processing_state'), original_unchanged=original == target.content,
                    reparse_state=rev2.processing_state, carried_forward=rev2.report.get('carried_forward'),
                    edit_carried=carried_block is not None and carried_block.content == edited_text,
                    reparse_same_block_ids=same_blocks, published_revision=str(doc.current_revision_id),
                    seconds=round(time.monotonic() - started, 1))
                rows.append(row)
                (root / 'summary.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
                print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS {schema} CASCADE'))
        await admin.dispose()
    bad = [r for r in rows if r['page_http_errors'] or r['storage_or_notes_leaks'] or r['blocks_rebuilt_from_slices'] != r['blocks']
           or r['blocks_seen_http'] != r['blocks'] or r['grep_found_on_its_page'] != r['grep_pages']
           or r['grep_ranges_exact'] != r['grep_pages'] or r['student_draft_status'] != 404
           or not r['edit_carried'] or not r['original_unchanged'] or r['reparse_state'] != 'parsed'
           or r['published_revision'] != 'None']
    if bad:
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
