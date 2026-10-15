"""
test_monitoring.py — what the Grafana login may read, and that the dashboards
need nothing more.

Grafana is viewed without a login on the internal network, and a viewer can
send any SQL to its PostgreSQL source, so the login's grants are the real limit.
"""

import json
import re
from pathlib import Path

from scripts.monitoring_reader import GRANTS_PATH, grant_statements

DASHBOARDS = Path(__file__).parents[2] / 'monitoring' / 'grafana' / 'dashboards'


def granted() -> dict[str, set[str]]:
    tables = {}
    for stmt in grant_statements(GRANTS_PATH.read_text()):
        found = re.match(r'GRANT SELECT \(([^)]*)\) ON (\w+) TO grafana_reader;$', ' '.join(stmt.split()))
        if found:
            tables[found[2]] = {c.strip() for c in found[1].split(',')}
    return tables


def test_only_named_columns_are_granted_and_nothing_personal():
    stmts = [' '.join(s.split()) for s in grant_statements(GRANTS_PATH.read_text())]
    assert all(s.startswith(('GRANT SELECT (', 'GRANT USAGE ON SCHEMA public')) for s in stmts)
    tables = granted()
    assert 'users' not in tables
    assert 'content' not in tables['messages']           # the answers
    assert not {'storage_path', 'content_hash'} & tables['documents']


def test_every_table_the_dashboards_read_is_granted():
    tables = granted()
    for path in DASHBOARDS.glob('*.json'):
        for panel in json.loads(path.read_text())['panels']:
            for target in panel.get('targets', []):
                sql = target.get('rawSql')
                if sql:
                    used = set(re.findall(r'\b(?:FROM|JOIN)\s+(\w+)', sql)) - {'runs'}
                    assert used <= set(tables), (path.name, panel['title'], used - set(tables))
