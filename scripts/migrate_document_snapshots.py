"""Explicit PostgreSQL schema migration; defaults to read-only dry-run.

Usage: python -m scripts.migrate_document_snapshots --database-url URL [--apply]
Never selects the application's database implicitly. Downgrade deletes nothing:
roll back an uncommitted transaction, or disable feature flags after deployment.
"""
import argparse
import asyncio
import hashlib
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

VERSION = 'm1_document_snapshots_v1'
SQL_PATH = Path(__file__).with_name('ingestion_agent_schema.sql')
# Each delivered file keeps its own version and checksum. A later change is a
# new file appended here, never an edit of a file that may already be applied.
M2_VERSION = 'm2_structured_ingest_v1'
M2_SQL_PATH = Path(__file__).with_name('ingestion_agent_schema_m2.sql')
M2C_VERSION = 'm2c_structured_review_v1'
M2C_SQL_PATH = Path(__file__).with_name('ingestion_agent_schema_m2c.sql')
M3A_VERSION = 'm3a_pdf_parse_v1'
M3A_SQL_PATH = Path(__file__).with_name('ingestion_agent_schema_m3a.sql')
M3B_VERSION = 'm3b_pdf_index_v1'
M3B_SQL_PATH = Path(__file__).with_name('ingestion_agent_schema_m3b.sql')
MIGRATIONS = ((VERSION, SQL_PATH), (M2_VERSION, M2_SQL_PATH), (M2C_VERSION, M2C_SQL_PATH),
              (M3A_VERSION, M3A_SQL_PATH), (M3B_VERSION, M3B_SQL_PATH))


async def _apply_one(conn, version, path):
    sql = path.read_text()
    checksum = hashlib.sha256(sql.encode()).hexdigest()
    previous = await conn.scalar(text('SELECT checksum FROM document_schema_migrations WHERE version=:version'), {'version': version})
    if previous:
        if previous != checksum:
            raise ValueError('migration_checksum_mismatch')
        return False
    for stmt in sql.split('-- statement')[1:]:
        if stmt.strip():
            await conn.execute(text(stmt))
    await conn.execute(text('INSERT INTO document_schema_migrations(version,checksum) VALUES (:version,:checksum)'),
                       {'version': version, 'checksum': checksum})
    return True


async def apply_migration(conn):
    """Apply every pending version in order, in the caller's one transaction."""
    if conn.dialect.name != 'postgresql':
        raise ValueError('M1 migration requires PostgreSQL')
    # Serialize even first application (before the ledger exists).
    await conn.execute(text("SELECT pg_advisory_xact_lock(719202601)"))
    await conn.execute(text('CREATE TABLE IF NOT EXISTS document_schema_migrations '
                           '(version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())'))
    applied = [await _apply_one(conn, version, path) for version, path in MIGRATIONS]
    return 'applied' if any(applied) else 'already_applied'


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    engine = create_async_engine(args.database_url)
    try:
        async with engine.begin() as conn:
            if args.apply:
                print(await apply_migration(conn))
            else:
                if conn.dialect.name != 'postgresql':
                    raise ValueError('M1 migration requires PostgreSQL')
                exists = await conn.scalar(text("SELECT to_regclass('public.documents') IS NOT NULL"))
                print({'mode': 'dry-run', 'versions': [version for version, _ in MIGRATIONS],
                       'legacy_documents_table': exists, 'new_tables': 14,
                       'content_migration': False, 'destructive_down': False})
    finally:
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
