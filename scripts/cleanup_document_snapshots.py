"""Run due cleanup jobs once. Schedule externally; explicit DB/root required."""
import argparse
import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from backend.app.models import DocumentCleanupJob, _utcnow
from backend.app.services.document_cleanup import run_cleanup


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--storage-root', required=True)
    args = parser.parse_args()
    engine = create_async_engine(args.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            ids = list((await db.scalars(select(DocumentCleanupJob.document_id).where(
                DocumentCleanupJob.state != 'complete', DocumentCleanupJob.next_attempt_at <= _utcnow()))).all())
        for doc_id in ids:
            async with factory() as db:
                async with db.begin():
                    job = await run_cleanup(db, document_id=doc_id, storage_root=args.storage_root)
                print({'document_id': str(doc_id), 'state': job.state, 'attempts': job.attempts})
    finally:
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
