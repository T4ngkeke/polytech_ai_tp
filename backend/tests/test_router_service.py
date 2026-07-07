"""
test_router_service.py — [v8.0] full router-decision telemetry.

Every LLM-router decision is logged to RouterQueryLog v2 (route + resolved number
+ source attribution + degraded / is_test flags + model / prompt version), the
labelled source for a future distilled router. Write-only; runs on SQLite.
"""

import uuid

import pytest
from sqlalchemy import select

from backend.app.models import RouterQueryLog
from backend.app.services.router_service import log_decision


@pytest.mark.asyncio
async def test_log_decision_persists_v2_row(db_session):
    lab_id = uuid.uuid4()

    await log_decision(
        db_session, message="comment faire l'exercice 3 ?", route="exercise",
        exercise_number=3, number_source="explicit", degraded=False,
        latency_ms=42, model_name="ministral-9b", prompt_version="v8.0-1",
        lab_id=lab_id, is_test=False,
    )

    rows = (await db_session.execute(select(RouterQueryLog))).scalars().all()
    assert len(rows) == 1
    r = rows[0]
    assert r.message == "comment faire l'exercice 3 ?"
    assert r.route == "exercise"
    assert r.exercise_number == 3
    assert r.number_source == "explicit"
    assert r.degraded is False
    assert r.latency_ms == 42
    assert r.model_name == "ministral-9b"
    assert r.prompt_version == "v8.0-1"
    assert r.lab_id == lab_id
    assert r.is_test is False


@pytest.mark.asyncio
async def test_log_decision_degraded_fallback_defaults(db_session):
    # A timeout/failure fallback: degraded row, no number, minimal metadata.
    await log_decision(
        db_session, message="et la suite ?", route="rag",
        exercise_number=None, number_source="none", degraded=True,
        latency_ms=2000,
    )

    r = (await db_session.execute(select(RouterQueryLog))).scalars().one()
    assert r.route == "rag"
    assert r.degraded is True
    assert r.number_source == "none"
    assert r.exercise_number is None
    assert r.is_test is False


@pytest.mark.asyncio
async def test_log_decision_marks_test_drive(db_session):
    await log_decision(
        db_session, message="drive", route="direct", exercise_number=None,
        number_source="none", degraded=False, latency_ms=1, is_test=True,
    )
    r = (await db_session.execute(select(RouterQueryLog))).scalars().one()
    assert r.is_test is True
