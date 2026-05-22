"""
test_admin.py — TDD tests for backend/app/routers/admin.py

Written BEFORE the implementation (RED phase).

Coverage
--------
- GET    /api/admin/users           — list non-deleted users (admin only)
- POST   /api/admin/users           — create a new user (admin only)
- PUT    /api/admin/users/{id}/quota — update daily_token_quota
- DELETE /api/admin/users/{id}       — soft-delete a user
- RBAC enforcement: student → 403, teacher → 403, admin → allowed
"""

import json
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import create_access_token, hash_password
from backend.app.database import get_db
from backend.app.main import app
from backend.app.models import User, UserRole


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(
    role: UserRole = UserRole.student,
    is_deleted: bool = False,
    username: str | None = None,
) -> User:
    return User(
        id=uuid.uuid4(),
        username=username or f"user_{uuid.uuid4().hex[:8]}",
        hashed_password=hash_password("password1"),
        role=role,
        daily_token_quota=50_000,
        is_deleted=is_deleted,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seed_users(db_session: AsyncSession) -> dict[str, User]:
    """Create admin, teacher, 2 students (one deleted) and return a dict."""
    admin = _make_user(role=UserRole.admin, username="admin1")
    teacher = _make_user(role=UserRole.teacher, username="teacher1")
    student_a = _make_user(role=UserRole.student, username="student_a")
    student_b = _make_user(role=UserRole.student, username="student_b")
    deleted = _make_user(role=UserRole.student, username="deleted_s", is_deleted=True)

    db_session.add_all([admin, teacher, student_a, student_b, deleted])
    await db_session.commit()
    for u in [admin, teacher, student_a, student_b, deleted]:
        await db_session.refresh(u)

    return {
        "admin": admin,
        "teacher": teacher,
        "student_a": student_a,
        "student_b": student_b,
        "deleted": deleted,
    }


@pytest_asyncio.fixture
async def admin_client(db_session: AsyncSession, seed_users) -> AsyncClient:
    """AsyncClient authenticated as an admin user."""
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(user_id=seed_users["admin"].id)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def student_client(db_session: AsyncSession, seed_users) -> AsyncClient:
    """AsyncClient authenticated as a student user."""
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(user_id=seed_users["student_a"].id)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def teacher_client(db_session: AsyncSession, seed_users) -> AsyncClient:
    """AsyncClient authenticated as a teacher user."""
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(user_id=seed_users["teacher"].id)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client
    app.dependency_overrides.clear()


# ===================================================================
# 1. GET /api/admin/users
# ===================================================================


class TestListUsers:
    async def test_admin_gets_list_of_non_deleted_users(
        self, admin_client: AsyncClient, seed_users
    ):
        resp = await admin_client.get("/api/admin/users")
        assert resp.status_code == 200
        data = resp.json()
        # Should return 4 users (admin, teacher, student_a, student_b)
        # The soft-deleted user must NOT appear.
        usernames = [u["username"] for u in data]
        assert "deleted_s" not in usernames
        assert len(data) == 4

    async def test_response_contains_expected_fields(
        self, admin_client: AsyncClient, seed_users
    ):
        resp = await admin_client.get("/api/admin/users")
        assert resp.status_code == 200
        user = resp.json()[0]
        assert "id" in user
        assert "username" in user
        assert "role" in user
        assert "daily_token_quota" in user
        assert "is_deleted" in user

    async def test_student_is_rejected_with_403(
        self, student_client: AsyncClient
    ):
        resp = await student_client.get("/api/admin/users")
        assert resp.status_code == 403

    async def test_teacher_is_rejected_with_403(
        self, teacher_client: AsyncClient
    ):
        resp = await teacher_client.get("/api/admin/users")
        assert resp.status_code == 403

    async def test_unauthenticated_is_rejected_with_401(
        self, db_session: AsyncSession
    ):
        async def override_get_db():
            yield db_session
        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/admin/users")
        app.dependency_overrides.clear()
        assert resp.status_code == 401


# ===================================================================
# 2. POST /api/admin/users
# ===================================================================


class TestCreateUser:
    async def test_admin_creates_student(
        self, admin_client: AsyncClient
    ):
        resp = await admin_client.post(
            "/api/admin/users",
            json={
                "username": "new_student",
                "password": "securePass1",
                "role": "student",
                "daily_token_quota": 30_000,
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["username"] == "new_student"
        assert body["role"] == "student"
        assert body["daily_token_quota"] == 30_000
        assert body["is_deleted"] is False
        assert "id" in body

    async def test_duplicate_username_returns_409(
        self, admin_client: AsyncClient, seed_users
    ):
        resp = await admin_client.post(
            "/api/admin/users",
            json={
                "username": "student_a",  # already exists
                "password": "securePass1",
            },
        )
        assert resp.status_code == 409

    async def test_student_cannot_create_user(
        self, student_client: AsyncClient
    ):
        resp = await student_client.post(
            "/api/admin/users",
            json={"username": "hacker", "password": "securePass1"},
        )
        assert resp.status_code == 403

    async def test_missing_required_fields_returns_422(
        self, admin_client: AsyncClient
    ):
        resp = await admin_client.post("/api/admin/users", json={})
        assert resp.status_code == 422


# ===================================================================
# 3. PUT /api/admin/users/{user_id}/quota
# ===================================================================


class TestUpdateQuota:
    async def test_admin_updates_quota(
        self, admin_client: AsyncClient, seed_users
    ):
        student = seed_users["student_a"]
        resp = await admin_client.put(
            f"/api/admin/users/{student.id}/quota",
            json={"daily_token_quota": 10_000},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["daily_token_quota"] == 10_000
        assert body["id"] == str(student.id)

    async def test_nonexistent_user_returns_404(
        self, admin_client: AsyncClient
    ):
        resp = await admin_client.put(
            f"/api/admin/users/{uuid.uuid4()}/quota",
            json={"daily_token_quota": 100},
        )
        assert resp.status_code == 404

    async def test_negative_quota_returns_422(
        self, admin_client: AsyncClient, seed_users
    ):
        student = seed_users["student_a"]
        resp = await admin_client.put(
            f"/api/admin/users/{student.id}/quota",
            json={"daily_token_quota": -5},
        )
        assert resp.status_code == 422

    async def test_student_cannot_update_quota(
        self, student_client: AsyncClient, seed_users
    ):
        resp = await student_client.put(
            f"/api/admin/users/{seed_users['student_b'].id}/quota",
            json={"daily_token_quota": 999},
        )
        assert resp.status_code == 403


# ===================================================================
# 4. DELETE /api/admin/users/{user_id}
# ===================================================================


class TestDeleteUser:
    async def test_admin_soft_deletes_user(
        self, admin_client: AsyncClient, seed_users, db_session: AsyncSession
    ):
        student = seed_users["student_a"]
        resp = await admin_client.delete(f"/api/admin/users/{student.id}")
        assert resp.status_code == 204

        # Verify the user is still in the DB but is_deleted == True
        await db_session.refresh(student)
        assert student.is_deleted is True

    async def test_deleting_nonexistent_user_returns_404(
        self, admin_client: AsyncClient
    ):
        resp = await admin_client.delete(f"/api/admin/users/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_deleting_already_deleted_user_returns_404(
        self, admin_client: AsyncClient, seed_users
    ):
        deleted = seed_users["deleted"]
        resp = await admin_client.delete(f"/api/admin/users/{deleted.id}")
        assert resp.status_code == 404

    async def test_student_cannot_delete(
        self, student_client: AsyncClient, seed_users
    ):
        resp = await student_client.delete(
            f"/api/admin/users/{seed_users['student_b'].id}"
        )
        assert resp.status_code == 403
