"""Create or update the read-only PostgreSQL login that Grafana uses.

Usage: GRAFANA_DB_PASSWORD=... python -m scripts.monitoring_reader --database-url URL

The login `grafana_reader` can read only the columns listed in
monitoring/reader_grants.sql, every transaction is read-only, and a query
stops after 10 s. Running it again sets the password and the grants again.
"""
import argparse
import asyncio
import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROLE = 'grafana_reader'
GRANTS_PATH = Path(__file__).parents[1] / 'monitoring' / 'reader_grants.sql'


def grant_statements(sql: str) -> list[str]:
    return [stmt.strip() for stmt in sql.split('-- statement')[1:] if stmt.strip()]


async def setup(conn, password: str) -> None:
    exists = await conn.scalar(text('SELECT 1 FROM pg_roles WHERE rolname = :r'), {'r': ROLE})
    verb = 'ALTER' if exists else 'CREATE'
    # A role statement takes no bind parameters; format() quotes the password.
    stmt = await conn.scalar(text(f"SELECT format('{verb} ROLE {ROLE} LOGIN PASSWORD %L', CAST(:p AS text))"),
                             {'p': password})
    await conn.execute(text(stmt))
    await conn.execute(text(f'ALTER ROLE {ROLE} SET default_transaction_read_only = on'))
    await conn.execute(text(f"ALTER ROLE {ROLE} SET statement_timeout = '10s'"))
    database = await conn.scalar(text('SELECT current_database()'))
    await conn.execute(text(f'GRANT CONNECT ON DATABASE "{database}" TO {ROLE}'))
    for stmt in grant_statements(GRANTS_PATH.read_text()):
        await conn.execute(text(stmt))


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', required=True)
    args = parser.parse_args()
    password = os.environ.get('GRAFANA_DB_PASSWORD', '')
    if len(password) < 16:
        raise SystemExit('Set GRAFANA_DB_PASSWORD (16 characters or more).')
    engine = create_async_engine(args.database_url)
    try:
        async with engine.begin() as conn:
            await setup(conn, password)
    finally:
        await engine.dispose()
    print(f'{ROLE}: ready')


if __name__ == '__main__':
    asyncio.run(main())
