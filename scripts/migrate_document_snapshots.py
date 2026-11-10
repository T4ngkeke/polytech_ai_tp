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
M4C_VERSION = 'm4c_research_persistence_v1'
M4C_SQL_PATH = Path(__file__).with_name('ingestion_agent_schema_m4c.sql')
M4E_VERSION = 'm4e_clarification_v1'
M4E_SQL_PATH = Path(__file__).with_name('ingestion_agent_schema_m4e.sql')
M4F_VERSION = 'm4f_answer_modes_v1'
M4F_SQL_PATH = Path(__file__).with_name('ingestion_agent_schema_m4f.sql')
M4G_VERSION = 'm4g_picture_captions_v1'
M4G_SQL_PATH = Path(__file__).with_name('ingestion_agent_schema_m4g.sql')
M4H_VERSION = 'm4h_job_not_before_v1'
M4H_SQL_PATH = Path(__file__).with_name('ingestion_agent_schema_m4h.sql')
M4I_VERSION = 'm4i_quota_week_v1'
M4I_SQL_PATH = Path(__file__).with_name('ingestion_agent_schema_m4i.sql')
M4J_VERSION = 'm4j_rule_items_v1'
M4J_SQL_PATH = Path(__file__).with_name('ingestion_agent_schema_m4j.sql')
M4K_VERSION = 'm4k_exam_files_v1'
M4K_SQL_PATH = Path(__file__).with_name('ingestion_agent_schema_m4k.sql')
M4L_VERSION = 'm4l_tour_v1'
M4L_SQL_PATH = Path(__file__).with_name('ingestion_agent_schema_m4l.sql')
M4M_VERSION = 'm4m_rule_source_v1'
M4M_SQL_PATH = Path(__file__).with_name('ingestion_agent_schema_m4m.sql')
M4N_VERSION = 'm4n_reset_outcome_v1'
M4N_SQL_PATH = Path(__file__).with_name('ingestion_agent_schema_m4n.sql')
MIGRATIONS = ((VERSION, SQL_PATH), (M2_VERSION, M2_SQL_PATH), (M2C_VERSION, M2C_SQL_PATH),
              (M3A_VERSION, M3A_SQL_PATH), (M3B_VERSION, M3B_SQL_PATH),
              (M4C_VERSION, M4C_SQL_PATH), (M4E_VERSION, M4E_SQL_PATH),
              (M4F_VERSION, M4F_SQL_PATH), (M4G_VERSION, M4G_SQL_PATH),
              (M4H_VERSION, M4H_SQL_PATH), (M4I_VERSION, M4I_SQL_PATH),
              (M4J_VERSION, M4J_SQL_PATH),
              (M4K_VERSION, M4K_SQL_PATH),
              (M4L_VERSION, M4L_SQL_PATH),
              (M4M_VERSION, M4M_SQL_PATH),
              (M4N_VERSION, M4N_SQL_PATH))


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
