"""
test_router_service.py — [v7.1] low-confidence routing telemetry.

When the kNN router falls back below threshold, the query is logged so a future
BERT/XLM-R router has real multilingual training data. Write-only; runs on SQLite.
"""

import uuid

import pytest
from sqlalchemy import select

from backend.app.models import RouterQueryLog
from backend.app.services.router_service import log_low_confidence_query


@pytest.mark.asyncio
async def test_log_low_confidence_query_persists_row(db_session):
    lab_id = uuid.uuid4()

    await log_low_confidence_query(
        db_session, message="??? truc bizarre", route="rag",
        top_similarity=0.12, lab_id=lab_id,
    )

    rows = (await db_session.execute(select(RouterQueryLog))).scalars().all()
    assert len(rows) == 1
    assert rows[0].message == "??? truc bizarre"
    assert rows[0].chosen_route == "rag"
    assert abs(rows[0].top_similarity - 0.12) < 1e-6
    assert rows[0].lab_id == lab_id
