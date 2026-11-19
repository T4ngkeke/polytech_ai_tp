"""Run the due cleanup jobs of deleted documents once, by hand.

The worker already runs them between its jobs (document_cleanup.run_due_cleanups);
this is for a stack without a worker. Explicit database URL and storage root.
"""
import argparse
import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.services.document_cleanup import run_due_cleanups


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--storage-root', required=True)
    args = parser.parse_args()
    engine = create_async_engine(args.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            print({'jobs_run': await run_due_cleanups(db, storage_root=args.storage_root, limit=1000)})
    finally:
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
