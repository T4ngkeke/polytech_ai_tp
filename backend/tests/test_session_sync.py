"""
test_session_sync.py — another window of the same conversation: whether an
answer is being written (GET /api/chat/sessions/{id}/state) and stopping it
from there (POST /api/chat/sessions/{id}/stop).
"""

import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from backend.app.models import Message, SenderType, Session
from backend.app.routers import chat as chat_router
from backend.tests.conftest import make_client, make_user


@pytest.mark.asyncio
async def test_state_counts_current_questions_and_stop_cancels_the_answer(db_session):
    student = make_user()
    db_session.add(student)
    await db_session.flush()
    session = Session(id=uuid.uuid4(), user_id=student.id)
    db_session.add(session)
    await db_session.flush()
    old = Message(id=uuid.uuid4(), session_id=session.id, sender=SenderType.user, content="v1",
                  superseded_at=datetime.now(timezone.utc))
    db_session.add(old)
    await db_session.flush()
    new = Message(id=uuid.uuid4(), session_id=session.id, sender=SenderType.user, content="v2", version_of=old.id)
    db_session.add(new)
    await db_session.commit()

    writing = asyncio.create_task(asyncio.sleep(60))     # the answer another window receives
    chat_router._running[session.id] = writing
    try:
        async with await make_client(db_session, student) as client:
            state = (await client.get(f"/api/chat/sessions/{session.id}/state")).json()
            assert state == {"running": True, "questions": 1, "last_question_id": str(new.id)}
            assert (await client.post(f"/api/chat/sessions/{session.id}/stop")).json() == {"stopped": True}
            await asyncio.sleep(0)
            assert writing.cancelled()
            assert (await client.get(f"/api/chat/sessions/{session.id}/state")).json()["running"] is False
            assert (await client.post(f"/api/chat/sessions/{session.id}/stop")).json() == {"stopped": False}
        stranger = make_user()
        db_session.add(stranger)
        await db_session.commit()
        async with await make_client(db_session, stranger) as other:
            assert (await other.get(f"/api/chat/sessions/{session.id}/state")).status_code == 404
            assert (await other.post(f"/api/chat/sessions/{session.id}/stop")).status_code == 404
    finally:
        chat_router._running.pop(session.id, None)
        writing.cancel()

