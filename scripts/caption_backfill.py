"""Picture captions for PDF documents read before captions existed.

Usage: python -m scripts.caption_backfill --database-url URL [--limit N] [--all] [--apply]

For each active PDF document whose published version has no picture captions,
ask a new reading of the same file, as the teacher's "Read again" does: the
worker reads it, writes the captions, indexes it and publishes the new version
when every page was read. Teacher edits are carried forward as for any reread.
The worker takes the jobs one at a time; run this at night.

--all also rereads documents that already have captions (a new prompt or
model). A document with a reading already waiting is left alone. Captions must
be on in the admin model map, else nothing is asked. Without --apply it only
prints what it would ask.

Run it with the stack's feature flags, as the backend has them:
STRUCTURED_INGEST_ENABLED=true PDF_STRUCTURED_INGEST_ENABLED=true. A document
that cannot be asked is reported and skipped.
"""
import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.config import settings
from backend.app.models import (
    Class, Document, DocumentRevision, DocumentSource, IngestionJob, JobStatus, SystemConfig,
)
from backend.app.services import document_edits as edits
from backend.worker import picture_captions as pc


def has_captions(rev) -> bool:
    """The caption step ran on this reading and nothing failed or ran out of
    time. A document without pictures to caption counts as done."""
    report = ((rev.report or {}).get('render') or {}).get('captions') or {}
    return 'prompt_version' in report and not (report.get('error') or report.get('failed')
                                               or report.get('not_reached'))


async def captions_on(db) -> bool:
    value = await db.scalar(select(SystemConfig.value).where(SystemConfig.key == pc.ENABLED_KEY))
    return (value or '').strip().lower() == 'on'


async def plan(db, *, every: bool = False, limit: int | None = None) -> list[tuple[Document, DocumentRevision, str]]:
    """(document, published version, teacher id) to read again, oldest name first."""
    rows = (await db.execute(
        select(Document, DocumentRevision, Class.teacher_id)
        .join(DocumentRevision, DocumentRevision.id == Document.current_revision_id)
        .join(DocumentSource, DocumentSource.id == DocumentRevision.source_version_id)
        .join(Class, Class.id == Document.class_id)
        .where(Document.structured.is_(True), Document.lifecycle == 'active',
               DocumentSource.format == 'pdf', Class.is_deleted.is_(False))
        .order_by(Document.filename, Document.id))).all()
    waiting = set((await db.scalars(select(IngestionJob.document_id).where(
        IngestionJob.status.in_([JobStatus.queued, JobStatus.processing])))).all())
    chosen = [(doc, rev, teacher) for doc, rev, teacher in rows
              if doc.id not in waiting and (every or not has_captions(rev))]
    return chosen[:limit] if limit else chosen


async def ask_reading(db, doc, rev, teacher_id):
    await edits.request_reparse(db, document_id=doc.id, teacher_id=teacher_id,
                                expected_revision_id=rev.id, expected_generation=doc.generation)
    await db.commit()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not (settings.STRUCTURED_INGEST_ENABLED and settings.PDF_STRUCTURED_INGEST_ENABLED):
        print('STRUCTURED_INGEST_ENABLED and PDF_STRUCTURED_INGEST_ENABLED must be true, as in the stack')
        return
    engine = create_async_engine(args.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            if not await captions_on(db):
                print(f'captions are off ({pc.ENABLED_KEY} is not "on" in the model map): nothing to do')
                return
            todo = await plan(db, every=args.all, limit=args.limit)
            asked = 0
            for doc, rev, teacher_id in todo:
                print(f'{doc.filename} ({doc.id})')
                if args.apply:
                    try:
                        await ask_reading(db, doc, rev, teacher_id)
                        asked += 1
                    except ValueError as exc:      # VersionConflict, EditRejected
                        await db.rollback()
                        print(f'  skipped: {exc}')
            print(f'{len(todo)} document(s)' + (f', {asked} asked' if args.apply
                                                else ': dry run, nothing asked (add --apply)'))
    finally:
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
