"""
test_auth.py — Tests for auth endpoints and dependencies (v7).

Coverage
--------
- POST /api/auth/signup   — student self-registration
- POST /api/auth/login    — credential validation
- GET  /api/auth/me       — current user profile
- RBAC dependencies       — get_current_user, require_teacher, require_admin
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import (
    create_access_token,
    decode_access_token,
    get_current_user,
    hash_password,
    require_admin,
    require_teacher,
    verify_password,
)
from backend.app.database import get_db
from backend.app.main import app
from backend.app.models import User, UserRole
from tests.conftest import make_user


# ===================================================================
# 1. Password utilities
# ===================================================================


class TestPasswordUtils:
    def test_hash_returns_non_empty_string(self):
        assert len(hash_password("secret")) > 0

    def test_hash_is_not_plaintext(self):
        assert hash_password("secret") != "secret"

    def test_verify_correct_password(self):
        h = hash_password("correct")
        assert verify_password("correct", h) is True

    def test_verify_wrong_password(self):
        h = hash_password("correct")
        assert verify_password("wrong", h) is False

    def test_two_hashes_differ(self):
        assert hash_password("same") != hash_password("same")


# ===================================================================
# 2. JWT creation / decoding
# ===================================================================


class TestJWT:
    def test_roundtrip(self):
        uid = uuid.uuid4()
        token = create_access_token(uid)
        assert decode_access_token(token) == uid

    def test_payload_only_sub_exp(self):
        import jose.jwt as jwt
        from backend.app.config import settings

        token = create_access_token(uuid.uuid4())
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        assert set(payload.keys()) == {"sub", "exp"}

    def test_tampered_token_raises_401(self):
        token = create_access_token(uuid.uuid4())
        with pytest.raises(HTTPException) as exc:
            decode_access_token(token[:-4] + "XXXX")
        assert exc.value.status_code == 401

    def test_expired_token_raises_401(self):
        import jose.jwt as jwt
        from backend.app.config import settings

        payload = {"sub": str(uuid.uuid4()), "exp": datetime.now(timezone.utc) - timedelta(seconds=1)}
        token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
        with pytest.raises(HTTPException) as exc:
            decode_access_token(token)
        assert exc.value.status_code == 401


# ===================================================================
# 3. RBAC dependencies
# ===================================================================


class TestGetCurrentUser:
    @pytest_asyncio.fixture
    async def active_user(self, db_session: AsyncSession) -> User:
        user = make_user(role=UserRole.student)
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    async def test_valid_token_returns_user(self, db_session, active_user):
        from fastapi.security import HTTPAuthorizationCredentials
        token = create_access_token(active_user.id)
        auth = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        user = await get_current_user(auth=auth, db=db_session)
        assert user.id == active_user.id

    async def test_deleted_user_raises_401(self, db_session):
        from fastapi.security import HTTPAuthorizationCredentials
        user = make_user(is_deleted=True)
        db_session.add(user)
        await db_session.commit()
        token = create_access_token(user.id)
        auth = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        with pytest.raises(HTTPException) as exc:
            await get_current_user(auth=auth, db=db_session)
        assert exc.value.status_code == 401

    async def test_nonexistent_user_raises_401(self, db_session):
        from fastapi.security import HTTPAuthorizationCredentials
        token = create_access_token(uuid.uuid4())
        auth = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        with pytest.raises(HTTPException) as exc:
            await get_current_user(auth=auth, db=db_session)
        assert exc.value.status_code == 401


class TestRequireTeacher:
    async def test_student_rejected(self, db_session):
        user = make_user(role=UserRole.student)
        with pytest.raises(HTTPException) as exc:
            await require_teacher(current_user=user)
        assert exc.value.status_code == 403

    async def test_teacher_allowed(self, db_session):
        user = make_user(role=UserRole.teacher)
        result = await require_teacher(current_user=user)
        assert result.role == UserRole.teacher

    async def test_admin_allowed(self, db_session):
        user = make_user(role=UserRole.admin)
        result = await require_teacher(current_user=user)
        assert result.role == UserRole.admin


class TestRequireAdmin:
    async def test_student_rejected(self):
        user = make_user(role=UserRole.student)
        with pytest.raises(HTTPException) as exc:
            await require_admin(current_user=user)
        assert exc.value.status_code == 403

    async def test_teacher_rejected(self):
        user = make_user(role=UserRole.teacher)
        with pytest.raises(HTTPException) as exc:
            await require_admin(current_user=user)
        assert exc.value.status_code == 403

    async def test_admin_allowed(self):
        user = make_user(role=UserRole.admin)
        result = await require_admin(current_user=user)
        assert result.role == UserRole.admin


# ===================================================================
# 4. POST /api/auth/signup
# ===================================================================


class TestSignup:
    @pytest_asyncio.fixture
    async def client(self, db_session: AsyncSession):
        async def override_get_db():
            yield db_session
        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
        app.dependency_overrides.clear()

    async def test_signup_creates_student_returns_jwt(self, client):
        resp = await client.post("/api/auth/signup", json={"username": "newstudent", "password": "pass1234"})
        assert resp.status_code == 201
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"

    async def test_signup_duplicate_returns_400(self, client, db_session):
        user = make_user(username="existing")
        db_session.add(user)
        await db_session.commit()

        resp = await client.post("/api/auth/signup", json={"username": "existing", "password": "pass1234"})
        assert resp.status_code == 400

    async def test_signup_short_password_returns_422(self, client):
        resp = await client.post("/api/auth/signup", json={"username": "user1", "password": "12345"})
        assert resp.status_code == 422

    async def test_signup_missing_fields_returns_422(self, client):
        resp = await client.post("/api/auth/signup", json={})
        assert resp.status_code == 422


# ===================================================================
# 5. POST /api/auth/login
# ===================================================================


class TestLogin:
    @pytest_asyncio.fixture
    async def client(self, db_session: AsyncSession):
        user = User(
            username="loginuser",
            hashed_password=hash_password("testpass99"),
            role=UserRole.student,
        )
        db_session.add(user)
        await db_session.commit()

        async def override_get_db():
            yield db_session
        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
        app.dependency_overrides.clear()

    async def test_correct_credentials_return_token(self, client):
        resp = await client.post("/api/auth/login", json={"username": "loginuser", "password": "testpass99"})
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    async def test_wrong_password_returns_401(self, client):
        resp = await client.post("/api/auth/login", json={"username": "loginuser", "password": "wrong"})
        assert resp.status_code == 401

    async def test_unknown_user_returns_401(self, client):
        resp = await client.post("/api/auth/login", json={"username": "nobody", "password": "any"})
        assert resp.status_code == 401


# ===================================================================
# 6. GET /api/auth/me
# ===================================================================


class TestMe:
    @pytest_asyncio.fixture
    async def authed_client(self, db_session: AsyncSession):
        user = make_user(username="meuser", role=UserRole.student)
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        async def override_get_db():
            yield db_session
        app.dependency_overrides[get_db] = override_get_db
        token = create_access_token(user.id)
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as c:
            yield c, user
        app.dependency_overrides.clear()

    async def test_me_returns_user_profile(self, authed_client):
        client, user = authed_client
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "meuser"
        assert body["role"] == "student"
        assert body["id"] == str(user.id)


# ===================================================================
# 7. POST /api/auth/change-password  ([v7.2] self-service)
# ===================================================================


class TestChangePassword:
    @pytest_asyncio.fixture
    async def authed_client(self, db_session: AsyncSession):
        # make_user sets password "password1".
        user = make_user(username="pwuser", role=UserRole.student)
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        async def override_get_db():
            yield db_session
        app.dependency_overrides[get_db] = override_get_db
        token = create_access_token(user.id)
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as c:
            yield c, user
        app.dependency_overrides.clear()

    async def test_change_password_success(self, authed_client, db_session):
        client, user = authed_client
        resp = await client.post("/api/auth/change-password", json={
            "old_password": "password1", "new_password": "brandnew99",
        })
        assert resp.status_code == 200
        await db_session.refresh(user)
        assert verify_password("brandnew99", user.hashed_password)
        assert not verify_password("password1", user.hashed_password)

    async def test_wrong_old_password_returns_400(self, authed_client, db_session):
        client, user = authed_client
        resp = await client.post("/api/auth/change-password", json={
            "old_password": "not-it", "new_password": "brandnew99",
        })
        assert resp.status_code == 400
        await db_session.refresh(user)
        assert verify_password("password1", user.hashed_password)  # unchanged

    async def test_short_new_password_returns_422(self, authed_client):
        client, _ = authed_client
        resp = await client.post("/api/auth/change-password", json={
            "old_password": "password1", "new_password": "123",
        })
        assert resp.status_code == 422

    async def test_unauthenticated_returns_401(self, db_session):
        async def override_get_db():
            yield db_session
        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/auth/change-password", json={
                "old_password": "password1", "new_password": "brandnew99",
            })
        app.dependency_overrides.clear()
        assert resp.status_code == 401
