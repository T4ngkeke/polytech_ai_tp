"""
test_auth.py — TDD tests for backend/app/auth.py

Written BEFORE the implementation.  Each test describes the exact behavior
required; the tests must fail until auth.py is implemented.

Coverage
--------
- hash_password / verify_password (bcrypt utilities)
- create_access_token (JWT contains ONLY user_id, expires correctly)
- decode_access_token (happy path + tampered/expired tokens)
- get_current_user dependency (valid token, deleted user, nonexistent user)
- require_teacher dependency (student blocked, teacher allowed, admin allowed)
- require_admin dependency (student blocked, teacher blocked, admin allowed)
- POST /api/auth/login endpoint (correct credentials, wrong password, unknown user)
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import User, UserRole


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(
    role: UserRole = UserRole.student,
    is_deleted: bool = False,
    plain_password: str = "secret123",
) -> dict:
    """Return keyword args for creating a User ORM instance."""
    # We import hash_password here so that if auth.py doesn't exist yet the
    # test collection itself won't crash — the import error surfaces at test
    # execution time, producing the expected ImportError / AttributeError.
    from backend.app.auth import hash_password

    return dict(
        id=uuid.uuid4(),
        username=f"user_{uuid.uuid4().hex[:6]}",
        hashed_password=hash_password(plain_password),
        role=role,
        daily_token_quota=50_000,
        is_deleted=is_deleted,
    )


# ---------------------------------------------------------------------------
# 1. Password utilities
# ---------------------------------------------------------------------------


class TestPasswordUtils:
    def test_hash_password_returns_non_empty_string(self):
        from backend.app.auth import hash_password

        result = hash_password("mysecret")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_hash_is_not_plaintext(self):
        from backend.app.auth import hash_password

        result = hash_password("mysecret")
        assert result != "mysecret"

    def test_verify_password_correct_password(self):
        from backend.app.auth import hash_password, verify_password

        hashed = hash_password("correct")
        assert verify_password("correct", hashed) is True

    def test_verify_password_wrong_password(self):
        from backend.app.auth import hash_password, verify_password

        hashed = hash_password("correct")
        assert verify_password("wrong", hashed) is False

    def test_two_hashes_of_same_password_differ(self):
        """bcrypt salts hashes — identical inputs must not produce identical outputs."""
        from backend.app.auth import hash_password

        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2


# ---------------------------------------------------------------------------
# 2. JWT creation
# ---------------------------------------------------------------------------


class TestCreateAccessToken:
    def test_returns_non_empty_string(self):
        from backend.app.auth import create_access_token

        token = create_access_token(user_id=uuid.uuid4())
        assert isinstance(token, str)
        assert len(token) > 0

    def test_payload_contains_only_user_id_and_exp(self):
        """JWT must NOT contain role, quota, or any other user fields."""
        import jose.jwt as jwt

        from backend.app.auth import create_access_token
        from backend.app.config import settings

        uid = uuid.uuid4()
        token = create_access_token(user_id=uid)
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])

        assert str(uid) == payload["sub"], "sub must be the user_id as a string"
        # Only 'sub' and 'exp' are allowed
        allowed_keys = {"sub", "exp"}
        extra_keys = set(payload.keys()) - allowed_keys
        assert not extra_keys, f"JWT must not contain extra keys: {extra_keys}"

    def test_token_expires_after_configured_minutes(self):
        """exp claim must be approximately now + ACCESS_TOKEN_EXPIRE_MINUTES."""
        import jose.jwt as jwt

        from backend.app.auth import create_access_token
        from backend.app.config import settings

        before = datetime.now(timezone.utc).replace(microsecond=0)
        token = create_access_token(user_id=uuid.uuid4())
        after = datetime.now(timezone.utc).replace(microsecond=0)

        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)

        expected_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        # JWT exp is stored at second precision; allow ±2 s of clock skew.
        assert before + expected_delta <= exp <= after + expected_delta + timedelta(seconds=2)


# ---------------------------------------------------------------------------
# 3. JWT decoding
# ---------------------------------------------------------------------------


class TestDecodeAccessToken:
    def test_roundtrip_returns_correct_user_id(self):
        from backend.app.auth import create_access_token, decode_access_token

        uid = uuid.uuid4()
        token = create_access_token(user_id=uid)
        decoded_uid = decode_access_token(token)
        assert decoded_uid == uid

    def test_tampered_token_raises_401(self):
        from backend.app.auth import create_access_token, decode_access_token

        token = create_access_token(user_id=uuid.uuid4())
        tampered = token[:-4] + "XXXX"
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token(tampered)
        assert exc_info.value.status_code == 401

    def test_arbitrary_string_raises_401(self):
        from backend.app.auth import decode_access_token

        with pytest.raises(HTTPException) as exc_info:
            decode_access_token("not.a.jwt")
        assert exc_info.value.status_code == 401

    def test_expired_token_raises_401(self):
        from backend.app.auth import decode_access_token
        from backend.app.config import settings
        import jose.jwt as jwt

        expired_payload = {
            "sub": str(uuid.uuid4()),
            "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
        }
        token = jwt.encode(expired_payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token(token)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# 4. get_current_user dependency
# ---------------------------------------------------------------------------


class TestGetCurrentUser:
    @pytest_asyncio.fixture
    async def active_user(self, db_session: AsyncSession) -> User:
        user = User(**_make_user(role=UserRole.student, plain_password="pass123"))
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    @pytest_asyncio.fixture
    async def deleted_user(self, db_session: AsyncSession) -> User:
        user = User(**_make_user(role=UserRole.student, is_deleted=True, plain_password="pass123"))
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    async def test_valid_token_returns_user(self, db_session: AsyncSession, active_user: User):
        from backend.app.auth import create_access_token, get_current_user
        from fastapi.security import HTTPAuthorizationCredentials

        token = create_access_token(user_id=active_user.id)
        auth = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        user = await get_current_user(auth=auth, db=db_session)
        assert user.id == active_user.id

    async def test_deleted_user_raises_401(self, db_session: AsyncSession, deleted_user: User):
        from backend.app.auth import create_access_token, get_current_user
        from fastapi.security import HTTPAuthorizationCredentials

        token = create_access_token(user_id=deleted_user.id)
        auth = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(auth=auth, db=db_session)
        assert exc_info.value.status_code == 401

    async def test_nonexistent_user_id_raises_401(self, db_session: AsyncSession):
        from backend.app.auth import create_access_token, get_current_user
        from fastapi.security import HTTPAuthorizationCredentials

        token = create_access_token(user_id=uuid.uuid4())  # random UUID, not in DB
        auth = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(auth=auth, db=db_session)
        assert exc_info.value.status_code == 401

    async def test_invalid_token_raises_401(self, db_session: AsyncSession):
        from backend.app.auth import get_current_user
        from fastapi.security import HTTPAuthorizationCredentials

        auth = HTTPAuthorizationCredentials(scheme="Bearer", credentials="bad.token.here")
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(auth=auth, db=db_session)
        assert exc_info.value.status_code == 401

    async def test_queries_db_for_role_not_jwt(self, db_session: AsyncSession, active_user: User):
        """
        Stale token prevention: even if someone crafts a JWT, the dependency
        must verify the user still exists and is not deleted via a DB query.
        This test inserts a user, issues a token, soft-deletes the user in the
        DB, then verifies the dependency rejects the still-valid JWT.
        """
        from backend.app.auth import create_access_token, get_current_user
        from fastapi.security import HTTPAuthorizationCredentials

        token = create_access_token(user_id=active_user.id)
        auth = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        # Soft-delete user in DB after token was issued
        active_user.is_deleted = True
        db_session.add(active_user)
        await db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(auth=auth, db=db_session)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# 5. require_teacher dependency
# ---------------------------------------------------------------------------


class TestRequireTeacher:
    @pytest_asyncio.fixture
    async def student(self, db_session: AsyncSession) -> User:
        user = User(**_make_user(role=UserRole.student))
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    @pytest_asyncio.fixture
    async def teacher(self, db_session: AsyncSession) -> User:
        user = User(**_make_user(role=UserRole.teacher))
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    @pytest_asyncio.fixture
    async def admin(self, db_session: AsyncSession) -> User:
        user = User(**_make_user(role=UserRole.admin))
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    async def test_student_is_rejected_with_403(self, student: User):
        from backend.app.auth import require_teacher

        with pytest.raises(HTTPException) as exc_info:
            await require_teacher(current_user=student)
        assert exc_info.value.status_code == 403

    async def test_teacher_is_allowed(self, teacher: User):
        from backend.app.auth import require_teacher

        result = await require_teacher(current_user=teacher)
        assert result.id == teacher.id

    async def test_admin_is_allowed(self, admin: User):
        from backend.app.auth import require_teacher

        result = await require_teacher(current_user=admin)
        assert result.id == admin.id


# ---------------------------------------------------------------------------
# 6. require_admin dependency
# ---------------------------------------------------------------------------


class TestRequireAdmin:
    @pytest_asyncio.fixture
    async def student(self, db_session: AsyncSession) -> User:
        user = User(**_make_user(role=UserRole.student))
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    @pytest_asyncio.fixture
    async def teacher(self, db_session: AsyncSession) -> User:
        user = User(**_make_user(role=UserRole.teacher))
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    @pytest_asyncio.fixture
    async def admin(self, db_session: AsyncSession) -> User:
        user = User(**_make_user(role=UserRole.admin))
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    async def test_student_is_rejected_with_403(self, student: User):
        from backend.app.auth import require_admin

        with pytest.raises(HTTPException) as exc_info:
            await require_admin(current_user=student)
        assert exc_info.value.status_code == 403

    async def test_teacher_is_rejected_with_403(self, teacher: User):
        from backend.app.auth import require_admin

        with pytest.raises(HTTPException) as exc_info:
            await require_admin(current_user=teacher)
        assert exc_info.value.status_code == 403

    async def test_admin_is_allowed(self, admin: User):
        from backend.app.auth import require_admin

        result = await require_admin(current_user=admin)
        assert result.id == admin.id


# ---------------------------------------------------------------------------
# 7. POST /api/auth/login endpoint (HTTP-level via TestClient)
# ---------------------------------------------------------------------------


class TestLoginEndpoint:
    """
    These tests drive the router through the full HTTP stack using
    FastAPI's AsyncClient, with the DB session overridden via dependency
    injection.
    """

    @pytest_asyncio.fixture
    async def client_with_user(self, db_session: AsyncSession):
        """
        Returns (AsyncClient, plain_password) for a user created in the test DB.
        Overrides get_db to use the in-memory session.
        """
        from httpx import AsyncClient, ASGITransport
        from backend.app.main import app
        from backend.app.database import get_db
        from backend.app.auth import hash_password

        plain_pw = "testpass99"
        user = User(
            id=uuid.uuid4(),
            username="loginuser",
            hashed_password=hash_password(plain_pw),
            role=UserRole.student,
            daily_token_quota=50_000,
            is_deleted=False,
        )
        db_session.add(user)
        await db_session.commit()

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac, plain_pw

        app.dependency_overrides.clear()

    async def test_login_with_correct_credentials_returns_token(self, client_with_user):
        client, plain_pw = client_with_user
        resp = await client.post(
            "/api/auth/login",
            json={"username": "loginuser", "password": plain_pw},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"
        assert len(body["access_token"]) > 0

    async def test_login_token_contains_only_user_id(self, client_with_user):
        """JWT payload must have 'sub' (user_id) and 'exp' only — no role."""
        import jose.jwt as jwt
        from backend.app.config import settings

        client, plain_pw = client_with_user
        resp = await client.post(
            "/api/auth/login",
            json={"username": "loginuser", "password": plain_pw},
        )
        assert resp.status_code == 200
        token = resp.json()["access_token"]
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])

        allowed_keys = {"sub", "exp"}
        extra_keys = set(payload.keys()) - allowed_keys
        assert not extra_keys, f"JWT must not contain: {extra_keys}"

    async def test_login_wrong_password_returns_401(self, client_with_user):
        client, _ = client_with_user
        resp = await client.post(
            "/api/auth/login",
            json={"username": "loginuser", "password": "wrongpassword"},
        )
        assert resp.status_code == 401

    async def test_login_unknown_user_returns_401(self, client_with_user):
        client, _ = client_with_user
        resp = await client.post(
            "/api/auth/login",
            json={"username": "doesnotexist", "password": "anypassword"},
        )
        assert resp.status_code == 401

    async def test_login_missing_fields_returns_422(self, client_with_user):
        client, _ = client_with_user
        resp = await client.post("/api/auth/login", json={})
        assert resp.status_code == 422
