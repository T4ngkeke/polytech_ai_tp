"""
test_chat.py — TDD tests for POST /api/chat/stream
"""

import json
import uuid
from datetime import date
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import create_access_token, hash_password
from backend.app.database import get_db
from backend.app.main import app
from backend.app.models import (
    Message,
    SenderType,
    Session,
    TeacherRule,
    UsageStat,
    User,
    UserRole,
)


def _make_user(
    username: str,
    quota: int = 50000,
) -> User:
    return User(
        id=uuid.uuid4(),
        username=username,
        hashed_password=hash_password("password1"),
        role=UserRole.student,
        daily_token_quota=quota,
    )


@pytest_asyncio.fixture
async def seed_chat_data(db_session: AsyncSession) -> dict:
    student1 = _make_user("student1", quota=100)
    student2 = _make_user("student2", quota=100)
    db_session.add_all([student1, student2])
    await db_session.flush()

    # Session for student1
    sess1 = Session(id=uuid.uuid4(), user_id=student1.id)
    # Session for student2
    sess2 = Session(id=uuid.uuid4(), user_id=student2.id)
    db_session.add_all([sess1, sess2])
    await db_session.flush()

    # Add past messages to sess1
    m1 = Message(session_id=sess1.id, sender=SenderType.user, content="hi")
    m2 = Message(session_id=sess1.id, sender=SenderType.llm, content="hello")
    db_session.add_all([m1, m2])

    # Teacher rule for student1
    teacher = _make_user("teacher", quota=50000)
    teacher.role = UserRole.teacher
    db_session.add(teacher)
    await db_session.flush()
    
    rule = TeacherRule(
        teacher_id=teacher.id,
        student_id=student1.id,
        rules_json=json.dumps({"instruction": "Always speak in French"}),
        is_active=True,
    )
    db_session.add(rule)

    await db_session.commit()
    for obj in [student1, student2, sess1, sess2, rule]:
        await db_session.refresh(obj)

    return {"student1": student1, "student2": student2, "sess1": sess1, "sess2": sess2, "rule": rule}


@pytest_asyncio.fixture
async def client1(db_session: AsyncSession, seed_chat_data) -> AsyncClient:
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(user_id=seed_chat_data["student1"].id)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def mock_openai():
    with patch("backend.app.routers.chat.AsyncOpenAI") as mock:
        async def mock_create(*args, **kwargs):
            async def mock_generator():
                class Delta:
                    def __init__(self, content):
                        self.content = content
                class Choice:
                    def __init__(self, content):
                        self.delta = Delta(content)
                class Chunk:
                    def __init__(self, content, usage=None):
                        self.choices = [Choice(content)]
                        self.usage = usage
                
                yield Chunk("Bonjour")
                yield Chunk(" le", type("Usage", (), {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12})())
            return mock_generator()

        client_instance = mock.return_value
        client_instance.chat.completions.create = mock_create
        yield mock


class TestChatStream:
    async def test_unauthenticated_returns_401(self, db_session: AsyncSession):
        async def override_get_db():
            yield db_session
        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/chat/stream", json={"session_id": str(uuid.uuid4()), "message": "hello"})
        app.dependency_overrides.clear()
        assert resp.status_code == 401

    async def test_session_not_found_or_idor_returns_404(self, client1, seed_chat_data):
        # Accessing student2's session
        resp = await client1.post(
            "/api/chat/stream",
            json={"session_id": str(seed_chat_data["sess2"].id), "message": "hello"}
        )
        assert resp.status_code == 404

    async def test_quota_exceeded_returns_429(self, client1, seed_chat_data, db_session):
        student1 = seed_chat_data["student1"]
        usage = UsageStat(user_id=student1.id, date=date.today(), tokens_used=100)
        db_session.add(usage)
        await db_session.commit()

        resp = await client1.post(
            "/api/chat/stream",
            json={"session_id": str(seed_chat_data["sess1"].id), "message": "hello"}
        )
        assert resp.status_code == 429

    async def test_happy_path_streaming_and_bg_tasks(self, client1, seed_chat_data, db_session, mock_openai):
        sess1 = seed_chat_data["sess1"]
        
        # Patch AsyncSessionLocal so the background task uses the test SQLite engine
        from sqlalchemy.ext.asyncio import async_sessionmaker
        test_sessionmaker = async_sessionmaker(db_session.bind, expire_on_commit=False)
        
        with patch("backend.app.routers.chat.AsyncSessionLocal", test_sessionmaker):
            async with client1.stream("POST", "/api/chat/stream", json={"session_id": str(sess1.id), "message": "my test message"}) as resp:
                if resp.status_code != 200:
                    print(await resp.aread())
                assert resp.status_code == 200
                assert resp.headers["content-type"] == "text/event-stream; charset=utf-8"
                
                chunks = []
                async for chunk in resp.aiter_text():
                    chunks.append(chunk)

                # verify SSE formatting
                full_text = "".join(chunks)
                assert "data: Bonjour\n\n" in full_text
                assert "data:  le\n\n" in full_text

        # TestClient automatically runs BackgroundTasks after the response is complete
        
        # Check DB updates
        await db_session.refresh(sess1)
        assert sess1.applied_rule_id == seed_chat_data["rule"].id

        # Check messages
        result = await db_session.execute(select(Message).where(Message.session_id == sess1.id).order_by(Message.created_at))
        messages = result.scalars().all()
        assert len(messages) == 4
        assert messages[2].sender == SenderType.user
        assert messages[2].content == "my test message"
        
        assert messages[3].sender == SenderType.llm
        assert messages[3].content == "Bonjour le"
        assert messages[3].prompt_tokens == 10
        assert messages[3].completion_tokens == 2
        assert messages[3].total_tokens == 12

        # Check UsageStat upsert
        usage_res = await db_session.execute(select(UsageStat).where(UsageStat.user_id == seed_chat_data["student1"].id))
        usage = usage_res.scalar_one()
        assert usage.tokens_used == 12
        assert usage.request_count == 1
