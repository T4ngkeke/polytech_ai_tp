"""
test_feedback.py — [v8.0 §11B] student thumbs up/down on an assistant message.

POST /api/chat/messages/{id}/feedback sets Message.feedback (a free golden-set
label; a 👎 links back to the message's AgentTraceLog for review). A student may
only rate messages in their own session.
"""

import uuid

import pytest

from backend.app.models import (
    Message, MessageFeedback, SenderType, Session, UserRole,
)
from backend.tests.conftest import make_client, make_user


async def _seed_message(session, *, owner):
    sess = Session(id=uuid.uuid4(), user_id=owner.id, lab_id=None, title="Chat")
    session.add(sess)
    await session.flush()
    msg = Message(id=uuid.uuid4(), session_id=sess.id, sender=SenderType.llm,
                  content="an answer")
    session.add(msg)
    await session.commit()
    return msg


@pytest.mark.asyncio
async def test_student_can_rate_own_message(db_session):
    student = make_user(role=UserRole.student)
    db_session.add(student)
    await db_session.flush()
    msg = await _seed_message(db_session, owner=student)

    client = await make_client(db_session, student)
    try:
        resp = await client.post(
            f"/api/chat/messages/{msg.id}/feedback", json={"feedback": "down"}
        )
    finally:
        await client.aclose()

    assert resp.status_code == 200
    await db_session.refresh(msg)
    assert msg.feedback == MessageFeedback.down


@pytest.mark.asyncio
async def test_cannot_rate_another_students_message(db_session):
    owner = make_user(role=UserRole.student)
    intruder = make_user(role=UserRole.student)
    db_session.add_all([owner, intruder])
    await db_session.flush()
    msg = await _seed_message(db_session, owner=owner)

    client = await make_client(db_session, intruder)
    try:
        resp = await client.post(
            f"/api/chat/messages/{msg.id}/feedback", json={"feedback": "up"}
        )
    finally:
        await client.aclose()

    assert resp.status_code == 404
    await db_session.refresh(msg)
    assert msg.feedback is None
