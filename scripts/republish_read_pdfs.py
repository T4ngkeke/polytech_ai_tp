"""Apply the "every page read" rule to PDF versions indexed before it existed.

Usage: python -m scripts.republish_read_pdfs --database-url URL [--apply]

Until 2026-10-15 every indexed PDF version was marked partial, so published
documents showed a warning and new uploads stayed drafts nobody could publish
from the app. Now a version is ready when every page was read, and a ready
draft is published. This script:

- marks the document of a published partial version indexed when every page
  was read (a published version itself cannot change, so its own state stays);
- publishes the newest indexed draft that is newer than the published version
  (or the only one) when every page was read;
- writes the pages that were not read into the report and the document message.

Without --apply it only prints what it would change.
"""
import argparse
import asyncio
import copy

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.models import Document, DocumentPage, DocumentRevision, DocumentStatus
from backend.app.services import document_versions as versions


async def unread_pages(db, revision_id) -> list[int]:
    return sorted((await db.scalars(select(DocumentPage.source_slide_no).where(
        DocumentPage.revision_id == revision_id, DocumentPage.state != 'usable'))).all())


def with_unread(rev, unread):
    report = copy.deepcopy(rev.report or {})
    report.setdefault('index', {})['unread_pages'] = unread
    return report


async def fix_document(db, doc) -> list[str]:
    changes = []
    revisions = (await db.scalars(select(DocumentRevision).where(DocumentRevision.document_id == doc.id)
                                  .order_by(DocumentRevision.created_at, DocumentRevision.id))).all()
    current = next((r for r in revisions if r.id == doc.current_revision_id), None)
    if current is not None and current.processing_state == 'partial':
        unread = await unread_pages(db, current.id)
        if not unread:
            doc.status, doc.error_message = DocumentStatus.indexed, None
            changes.append('published version: every page read')
        else:
            doc.error_message = versions.unread_pages_message(unread)
            changes.append(f'published version: pages not read {unread}')
    newer = [r for r in revisions if r.publication_state == 'draft' and r.index_ready
             and r.processing_state == 'partial'
             and (current is None or r.created_at > current.created_at)]
    if newer:
        draft = newer[-1]
        unread = await unread_pages(db, draft.id)
        draft.report = with_unread(draft, unread)
        if unread:
            changes.append(f'draft kept for the teacher: pages not read {unread}')
        else:
            draft.processing_state = 'ready'
            await db.flush()
            await versions.publish_revision(db, document_id=doc.id, revision_id=draft.id,
                                            teacher_id=draft.created_by, expected_generation=doc.generation)
            changes.append('draft published')
    return changes


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    engine = create_async_engine(args.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            docs = (await db.scalars(select(Document).where(
                Document.structured.is_(True), Document.lifecycle == 'active')
                .order_by(Document.filename))).all()
            for doc in docs:
                for change in await fix_document(db, doc):
                    print(f'{doc.filename}: {change}')
            if args.apply:
                await db.commit()
                print('applied')
            else:
                await db.rollback()
                print('dry run: nothing changed (add --apply)')
    finally:
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
