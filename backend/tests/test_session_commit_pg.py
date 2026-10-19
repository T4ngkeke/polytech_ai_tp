"""New sessions must be visible before their successful HTTP response is sent."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.auth import create_access_token
from backend.app.database import get_db
from backend.app.main import app
from backend.app.models import Class, ClassStudent, Lab, Session, UserRole
from backend.tests.conftest import make_user


@pytest.mark.asyncio
async def test_created_session_is_committed_before_response_starts(pg_session):
    teacher = make_user(role=UserRole.teacher)
    student = make_user()
    pg_session.add_all([teacher, student])
    await pg_session.flush()
    course = Class(name="Commit timing", teacher_id=teacher.id, invite_code=uuid.uuid4().hex[:6])
    pg_session.add(course)
    await pg_session.flush()
    lab = Lab(name="Lab", class_id=course.id)
    pg_session.add(lab)
    pg_session.add(ClassStudent(class_id=course.id, student_id=student.id))
    await pg_session.commit()

    factory = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    title = "Immediately usable " + uuid.uuid4().hex
    visible_at_response_start = []

    async def request_db():
        async with factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def observe_response(scope, receive, send):
        async def observe_send(message):
            if message["type"] == "http.response.start" and message["status"] == 201:
                # A second connection models the next browser request, before
                # FastAPI runs the first request's dependency cleanup.
                async with factory() as observer:
                    row = await observer.scalar(select(Session.id).where(Session.title == title))
                    visible_at_response_start.append(row is not None)
            await send(message)
        await app(scope, receive, observe_send)

    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = request_db
    try:
        token = create_access_token(student.id)
        async with AsyncClient(transport=ASGITransport(app=observe_response), base_url="http://test",
                               headers={"Authorization": f"Bearer {token}"}) as client:
            response = await client.post(f"/api/student/labs/{lab.id}/sessions", json={"title": title})
            assert response.status_code == 201
        assert visible_at_response_start == [True]
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
