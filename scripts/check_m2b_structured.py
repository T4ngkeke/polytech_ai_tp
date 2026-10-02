"""M2-B real-file check: upload -> structured job -> parsed draft, on a test database.

Not content acceptance. It runs the real upload service, the real worker step
and the isolated renderer compose on each ODP, in a random schema of a
disposable *_test PostgreSQL database, with a new storage root. It never
starts, stops or reads the application's services, database or volumes.

  .venv/bin/python scripts/check_m2b_structured.py \
      --database-url postgresql+asyncpg://...:.../name_test --output-root /tmp/<new dir>
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))

from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from backend.app.config import settings  # noqa: E402
from backend.app.database import Base  # noqa: E402
from backend.app.models import (  # noqa: E402
    Class, Document, DocumentAsset, DocumentBlock, DocumentPage, DocumentRevision, DocumentSource,
    DocumentStagedObject, IngestionJob, Lab, User, UserRole,
)
from backend.app.services import structured_upload  # noqa: E402
from backend.app.services.document_cleanup import mark_deleted, run_cleanup  # noqa: E402
from backend.app.services.document_versions import VersionConflict, publish_revision  # noqa: E402
from backend.worker import structured_ingest as si  # noqa: E402
from backend.worker.slide_renderer import RenderSettings  # noqa: E402
from scripts.migrate_document_snapshots import apply_migration  # noqa: E402

NS_TEXT = '{urn:oasis:names:tc:opendocument:xmlns:text:1.0}'
NS_PRES = '{urn:oasis:names:tc:opendocument:xmlns:presentation:1.0}'


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def notes_lines(path: Path) -> list[str]:
    """Speaker-note paragraphs of the original, read here only to prove absence."""
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read('content.xml'))
    lines = []
    for notes in root.iter(NS_PRES + 'notes'):
        for para in notes.iter(NS_TEXT + 'p'):
            line = re.sub(r'\s+', ' ', ''.join(para.itertext())).strip()
            if len(line) >= 12:
                lines.append(line)
    return lines


def compose_runner(log_dir: Path):
    def run(task):
        env = dict(os.environ, TASK_DIR=str(task.task_dir), RENDERER_UID=str(os.getuid()),
                   RENDERER_GID=str(os.getgid()))
        cmd = ['docker', 'compose', '-p', 'edullm-m2b-test', '-f',
               str(REPOSITORY / 'docker-compose.renderer-test.yml'), 'run', '--rm', 'renderer']
        with (log_dir / f'{task.task_dir.parent.name}.log').open('w') as log:
            subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=660, cwd=REPOSITORY,
                           check=False)
    return run


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
    schema = 'm2b_real_' + uuid.uuid4().hex[:12]
    admin = create_async_engine(args.database_url, poolclass=NullPool)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_async_engine(args.database_url, poolclass=NullPool,
                                 connect_args={'server_settings': {'search_path': f'{schema},public'}})
    image = subprocess.check_output(['docker', 'image', 'inspect', 'edullm-slide-renderer:1',
                                     '--format', '{{.Id}}'], text=True).strip()
    worker = si.StructuredSettings(storage_root=storage, task_root=tasks,
                                   render=RenderSettings(want_page_images=True, timeout_seconds=660))
    rows = []
    try:
        async with engine.begin() as conn:
            await conn.execute(text('CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public'))
            await conn.run_sync(Base.metadata.create_all)
            await apply_migration(conn)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            teacher = User(id=uuid.uuid4(), username='m2b-check', hashed_password='x', role=UserRole.teacher)
            db.add(teacher)
            await db.flush()
            cls = Class(id=uuid.uuid4(), name='M2-B check', teacher_id=teacher.id, invite_code='m2bchk')
            db.add(cls)
            await db.flush()
            lab = Lab(id=uuid.uuid4(), class_id=cls.id, name='TP')
            db.add(lab)
            await db.commit()
            teacher_id, lab_id = teacher.id, lab.id
        for source in sorted(args.corpus.glob('*.odp')):
            before = sha256(source)
            row = {'file': source.name, 'source_sha256': before}
            started = time.monotonic()
            async with factory() as db:
                lab = await db.get(Lab, lab_id)
                result = await structured_upload.upload_odp(
                    db, teacher_id=teacher_id, lab=lab, shared=False, filename=source.name,
                    content=source.read_bytes(), language='fr', storage_root=storage)
                doc_id, rev_id, job_id = result.document.id, result.revision.id, result.job.id
                row['upload_seconds'] = round(time.monotonic() - started, 2)
                parse_started = time.monotonic()
                assert await si.process_structured_job(db, settings=worker, runner=compose_runner(logs))
                row['job_seconds'] = round(time.monotonic() - parse_started, 2)
                rev = await db.get(DocumentRevision, rev_id)
                job = await db.get(IngestionJob, job_id)
                doc = await db.get(Document, doc_id)
                for obj in (rev, job, doc):
                    await db.refresh(obj)
                pages = (await db.scalars(select(DocumentPage).where(DocumentPage.revision_id == rev_id))).all()
                blocks = (await db.scalars(select(DocumentBlock).where(DocumentBlock.revision_id == rev_id))).all()
                assets = (await db.scalars(select(DocumentAsset).where(
                    DocumentAsset.source_version_id == rev.source_version_id))).all()
                files_ok = all(sha256(storage / a.storage_key) == a.sha256 for a in assets)
                src = await db.get(DocumentSource, rev.source_version_id)
                stored_text = json.dumps([[b.content, b.source_text, b.issues] for b in blocks]
                                         + [rev.report, [p.issues for p in pages]], ensure_ascii=False)
                leaked = [line for line in notes_lines(source) if line in stored_text]
                # Same content again to the same document: no new revision or job.
                again = await structured_upload.upload_odp(
                    db, teacher_id=teacher_id, lab=lab, shared=False, filename=source.name,
                    content=source.read_bytes(), language='fr', storage_root=storage, mode='replace',
                    replace_document_id=doc_id, expected_generation=doc.generation)
                kinds = {}
                for a in assets:
                    kinds[a.kind] = kinds.get(a.kind, 0) + 1
                row.update(
                    job_status=job.status.value, job_error=job.error_message, attempts=job.attempts,
                    reader_version=rev.reader_version,
                    processing_state=rev.processing_state, publication_state=rev.publication_state,
                    index_ready=rev.index_ready, parse_quality=rev.report.get('parse_quality'),
                    render_status=(rev.report.get('render') or {}).get('status'),
                    render_seconds=(rev.report.get('render') or {}).get('seconds'),
                    visible_pages=len(pages), hidden_slides=rev.report.get('hidden_slide_count'),
                    notes_slides=rev.report.get('notes_slide_count'),
                    preview_verified=sum(p.preview_page_no is not None for p in pages),
                    preview_unproven=sum(p.preview_page_no is None for p in pages),
                    pages_with_preview_asset=sum(p.preview_asset_id is not None for p in pages),
                    blocks=len(blocks), blocks_searchable=sum(b.index_state == 'searchable' for b in blocks),
                    blocks_needs_review=sum(b.index_state == 'needs_review' for b in blocks),
                    blocks_not_complete=sum(b.source_completeness != 'complete' for b in blocks),
                    assets_by_kind=kinds, asset_files_hash_ok=files_ok,
                    stored_source_hash_ok=sha256(storage / src.storage_key) == before,
                    notes_lines_checked=len(notes_lines(source)), notes_lines_leaked=len(leaked),
                    same_content_again_reused=(not again.created and again.revision.id == rev_id
                                               and again.job.id == job_id),
                    doc_current_revision=str(doc.current_revision_id),
                )
            async with factory() as db:
                try:
                    await publish_revision(db, document_id=doc_id, revision_id=rev_id, teacher_id=teacher_id,
                                           expected_generation=doc.generation, acknowledge_issues=True)
                    publish = 'published'
                except VersionConflict as exc:
                    publish = f'refused:{exc}'
                await db.rollback()
                row['publish_attempt'] = publish
            row['original_unchanged'] = sha256(source) == before
            rows.append(row)
            (root / 'summary.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
            print(json.dumps(row, ensure_ascii=False), flush=True)
        # Deleting one document removes every stored file and row of it.
        async with factory() as db:
            doc = await db.scalar(select(Document).order_by(Document.filename).limit(1))
            files_before = sum(1 for p in (storage / str(doc.id)).rglob('*') if p.is_file())
            await mark_deleted(db, document_id=doc.id, teacher_id=teacher_id, expected_generation=doc.generation)
            await db.commit()
            job = await run_cleanup(db, document_id=doc.id, storage_root=storage)
            await db.commit()
            deletion = {'file': doc.filename or '(tombstone)', 'document_id': str(doc.id),
                        'files_before': files_before, 'cleanup_state': job.state,
                        'files_after': sum(1 for p in (storage / str(doc.id)).rglob('*') if p.is_file()),
                        'pages_after': await db.scalar(select(func.count()).select_from(DocumentPage).join(
                            DocumentRevision, DocumentRevision.id == DocumentPage.revision_id).where(
                            DocumentRevision.document_id == doc.id)),
                        'staged_rows_left': await db.scalar(select(func.count()).select_from(DocumentStagedObject))}
        environment = {'renderer_image': image, 'database': 'disposable *_test, random schema (dropped)',
                       'storage_root': 'new temporary directory',
                       'reader_versions': sorted({r['reader_version'] for r in rows})}
        (root / 'deletion.json').write_text(json.dumps(deletion, indent=2))
        (root / 'environment.json').write_text(json.dumps(environment, indent=2))
        print(json.dumps(deletion), flush=True)
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS {schema} CASCADE'))
        await admin.dispose()
    bad = [r for r in rows if r['processing_state'] != 'parsed' or not r['original_unchanged']
           or r['notes_lines_leaked'] or not r['asset_files_hash_ok'] or not r['same_content_again_reused']
           or not r['publish_attempt'].startswith('refused')]
    if bad or deletion['files_after'] or deletion['cleanup_state'] != 'complete':
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
