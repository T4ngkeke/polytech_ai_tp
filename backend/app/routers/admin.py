"""
routers/admin.py — Admin-only endpoints for user, quota, LLM config, CSV import,
global class/lab oversight, analytics, session purge, and maintenance pruning.

All endpoints are protected by ``require_admin`` — only users whose live
DB role is ``admin`` can access them.

Endpoints
---------
GET    /api/admin/users                      →  List all non-deleted users.
POST   /api/admin/users                      →  Create a new user.
PUT    /api/admin/users/{user_id}/quota       →  Update a user's daily token quota.
PUT    /api/admin/users/{user_id}/role        →  Change a user's role.
DELETE /api/admin/users/{user_id}            →  Soft-delete a user (is_deleted = True).
PUT    /api/admin/llm/config                 →  Upsert LLM config in SystemConfig table.
GET    /api/admin/llm/config                 →  Read current LLM config.
POST   /api/admin/users/import               →  CSV bulk import (dry-run or force).
GET    /api/admin/classes                    →  List all classes system-wide.
GET    /api/admin/classes/{class_id}/students →  List students enrolled in a class.
GET    /api/admin/labs                       →  List all labs system-wide.
PUT    /api/admin/classes/{class_id}/transfer →  Force-change teacher_id of a class.
GET    /api/admin/analytics                  →  Global token usage dashboard.
DELETE /api/admin/sessions/{session_id}      →  Hard-delete a session + messages.
POST   /api/admin/maintenance/prune          →  Hard-delete old sessions/messages.
"""

import csv
import io
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import hash_password, require_admin
from backend.app.database import get_db
from backend.app.models import Class, ClassStudent, Lab, Message, Session, SystemConfig, UsageStat, User, UserRole
from backend.app.schemas import (
    AdminAnalyticsResponse,
    ClassResponse,
    CSVImportPreviewResponse,
    DailyUsage,
    LabResponse,
    LLMConfigRequest,
    LLMConfigResponse,
    PruneRequest,
    PruneResponse,
    TransferClassRequest,
    UpdateQuotaRequest,
    UpdateRoleRequest,
    UserCreateRequest,
    UserResponse,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ===================================================================
# GET /api/admin/users
# ===================================================================


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[UserResponse]:
    """Return all non-deleted users with their role and daily_token_quota."""
    result = await db.execute(
        select(User).where(User.is_deleted.is_(False))
    )
    users = result.scalars().all()
    return [UserResponse.model_validate(u) for u in users]


# ===================================================================
# POST /api/admin/users
# ===================================================================


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    body: UserCreateRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """
    Create a new platform user.

    Returns 409 if the username already exists.
    """
    # Check for duplicate username
    existing = await db.execute(
        select(User).where(User.username == body.username)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{body.username}' is already taken",
        )

    user = User(
        username=body.username,
        hashed_password=hash_password(body.password),
        role=body.role,
        daily_token_quota=body.daily_token_quota,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return UserResponse.model_validate(user)


# ===================================================================
# PUT /api/admin/users/{user_id}/quota
# ===================================================================


@router.put("/users/{user_id}/quota", response_model=UserResponse)
async def update_quota(
    user_id: uuid.UUID,
    body: UpdateQuotaRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Update the daily token quota for a specific (non-deleted) user."""
    result = await db.execute(
        select(User).where(User.id == user_id, User.is_deleted.is_(False))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user.daily_token_quota = body.daily_token_quota
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return UserResponse.model_validate(user)


# ===================================================================
# PUT /api/admin/users/{user_id}/role
# ===================================================================


@router.put("/users/{user_id}/role", response_model=UserResponse)
async def update_role(
    user_id: uuid.UUID,
    body: UpdateRoleRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Change a user's role (e.g., student ↔ teacher)."""
    result = await db.execute(
        select(User).where(User.id == user_id, User.is_deleted.is_(False))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user.role = body.role
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return UserResponse.model_validate(user)


# ===================================================================
# DELETE /api/admin/users/{user_id}
# ===================================================================


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """
    Soft-delete a user: sets ``is_deleted = True`` without removing the row.

    Returns 404 if the user doesn't exist or is already deleted.
    """
    result = await db.execute(
        select(User).where(User.id == user_id, User.is_deleted.is_(False))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found or already deleted",
        )

    user.is_deleted = True
    db.add(user)
    await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===================================================================
# PUT /api/admin/llm/config
# ===================================================================


async def _upsert_config(db: AsyncSession, key: str, value: str) -> None:
    """Insert or update a SystemConfig row by key."""
    result = await db.execute(
        select(SystemConfig).where(SystemConfig.key == key)
    )
    config = result.scalar_one_or_none()
    if config is None:
        config = SystemConfig(key=key, value=value)
        db.add(config)
    else:
        config.value = value
        db.add(config)


@router.put("/llm/config", response_model=LLMConfigResponse)
async def update_llm_config(
    body: LLMConfigRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> LLMConfigResponse:
    """
    Upsert LLM connection parameters in the SystemConfig table.

    This enables zero-downtime model switching — the chat engine reads
    these values per-request instead of using static env vars.
    """
    await _upsert_config(db, "LLM_BASE_URL", body.base_url)
    await _upsert_config(db, "LLM_API_KEY", body.api_key)
    await _upsert_config(db, "LLM_MODEL", body.model)
    await db.flush()

    return LLMConfigResponse(
        base_url=body.base_url,
        api_key=body.api_key,
        model=body.model,
    )


# ===================================================================
# GET /api/admin/llm/config
# ===================================================================


@router.get("/llm/config", response_model=LLMConfigResponse)
async def get_llm_config(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> LLMConfigResponse:
    """Read the current LLM configuration from the SystemConfig table."""
    result = await db.execute(select(SystemConfig))
    configs = {row.key: row.value for row in result.scalars().all()}

    return LLMConfigResponse(
        base_url=configs.get("LLM_BASE_URL", ""),
        api_key=configs.get("LLM_API_KEY", ""),
        model=configs.get("LLM_MODEL", ""),
    )


# ===================================================================
# POST /api/admin/users/import
# ===================================================================


@router.post("/users/import", response_model=CSVImportPreviewResponse)
async def import_users_csv(
    file: UploadFile = File(...),
    force: bool = Query(False, description="If true, execute the import. Otherwise dry-run."),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> CSVImportPreviewResponse:
    """
    Bulk import users from a CSV file.

    Expected CSV columns: ``username``, ``password``, ``role`` (optional, defaults to student).

    - **Dry-run** (``?force=false``, default): Parse CSV, check against existing
      users. Return a JSON preview of how many users will be created vs. updated.
    - **Force** (``?force=true``): Execute the import. Create new users and
      update existing users' hashed passwords (upsert).
    """
    content = await file.read()
    text = content.decode("utf-8-sig")  # Handle BOM
    reader = csv.DictReader(io.StringIO(text))

    rows = list(reader)
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV file is empty or has no data rows",
        )

    # Validate required columns
    required_cols = {"username", "password"}
    if rows[0].keys() and not required_cols.issubset(set(rows[0].keys())):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"CSV must contain columns: {', '.join(required_cols)}. "
                   f"Found: {', '.join(rows[0].keys())}",
        )

    # Fetch existing usernames
    result = await db.execute(select(User.username))
    existing_usernames = {row[0] for row in result.all()}

    to_create = []
    to_update = []
    conflicts = []

    for i, row in enumerate(rows, start=2):  # row 1 is header
        username = row.get("username", "").strip()
        password = row.get("password", "").strip()
        role_str = row.get("role", "student").strip().lower()

        if not username or not password:
            conflicts.append({
                "row": i,
                "username": username,
                "reason": "Missing username or password",
            })
            continue

        if role_str not in ("student", "teacher", "admin"):
            role_str = "student"

        if username in existing_usernames:
            to_update.append({"username": username, "password": password, "role": role_str})
        else:
            to_create.append({"username": username, "password": password, "role": role_str})

    response = CSVImportPreviewResponse(
        total=len(rows),
        to_create=len(to_create),
        to_update=len(to_update),
        conflicts=conflicts,
        forced=force,
    )

    if force:
        # Execute: create new users
        for entry in to_create:
            user = User(
                username=entry["username"],
                hashed_password=hash_password(entry["password"]),
                role=entry["role"],
            )
            db.add(user)
        response.created = len(to_create)

        # Execute: update existing users' passwords
        for entry in to_update:
            res = await db.execute(
                select(User).where(User.username == entry["username"])
            )
            user = res.scalar_one()
            user.hashed_password = hash_password(entry["password"])
            db.add(user)
        response.updated = len(to_update)

        await db.flush()

    return response


# ===================================================================
# GET /api/admin/classes
# ===================================================================


@router.get("/classes", response_model=list[ClassResponse])
async def list_all_classes(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[ClassResponse]:
    """List all non-deleted classes system-wide (bypasses teacher ownership)."""
    result = await db.execute(
        select(Class)
        .where(Class.is_deleted.is_(False))
        .order_by(Class.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    classes = result.scalars().all()
    return [ClassResponse.model_validate(c) for c in classes]


# ===================================================================
# GET /api/admin/classes/{class_id}/students
# ===================================================================


@router.get("/classes/{class_id}/students", response_model=list[UserResponse])
async def list_class_students(
    class_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[UserResponse]:
    """List all students enrolled in a specific class."""
    # Verify class exists
    cls_result = await db.execute(select(Class).where(Class.id == class_id))
    if cls_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Class not found",
        )

    result = await db.execute(
        select(User)
        .join(ClassStudent, ClassStudent.student_id == User.id)
        .where(
            ClassStudent.class_id == class_id,
            User.is_deleted.is_(False),
        )
    )
    students = result.scalars().all()
    return [UserResponse.model_validate(s) for s in students]


# ===================================================================
# GET /api/admin/labs
# ===================================================================


@router.get("/labs", response_model=list[LabResponse])
async def list_all_labs(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[LabResponse]:
    """List all non-deleted labs system-wide."""
    result = await db.execute(
        select(Lab)
        .where(Lab.is_deleted.is_(False))
        .order_by(Lab.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    labs = result.scalars().all()
    return [LabResponse.model_validate(l) for l in labs]


# ===================================================================
# PUT /api/admin/classes/{class_id}/transfer
# ===================================================================


@router.put("/classes/{class_id}/transfer", response_model=ClassResponse)
async def transfer_class(
    class_id: uuid.UUID,
    body: TransferClassRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ClassResponse:
    """Force-change the teacher_id of a class to a new teacher."""
    # Verify class exists
    cls_result = await db.execute(
        select(Class).where(Class.id == class_id, Class.is_deleted.is_(False))
    )
    cls = cls_result.scalar_one_or_none()
    if cls is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Class not found",
        )

    # Verify new teacher exists and has teacher or admin role
    teacher_result = await db.execute(
        select(User).where(
            User.id == body.teacher_id,
            User.is_deleted.is_(False),
            User.role.in_([UserRole.teacher, UserRole.admin]),
        )
    )
    if teacher_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target teacher not found or does not have teacher/admin role",
        )

    cls.teacher_id = body.teacher_id
    db.add(cls)
    await db.flush()
    await db.refresh(cls)
    return ClassResponse.model_validate(cls)


# ===================================================================
# GET /api/admin/analytics
# ===================================================================


@router.get("/analytics", response_model=AdminAnalyticsResponse)
async def get_global_analytics(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminAnalyticsResponse:
    """Global token usage dashboard aggregated from UsageStat."""
    # Totals
    totals_result = await db.execute(
        select(
            func.coalesce(func.sum(UsageStat.tokens_used), 0),
            func.coalesce(func.sum(UsageStat.request_count), 0),
        )
    )
    row = totals_result.one()
    total_tokens = row[0]
    total_requests = row[1]

    # Daily breakdown
    daily_result = await db.execute(
        select(
            UsageStat.date,
            func.sum(UsageStat.tokens_used),
            func.sum(UsageStat.request_count),
        )
        .group_by(UsageStat.date)
        .order_by(UsageStat.date.desc())
        .limit(30)
    )
    daily_breakdown = [
        DailyUsage(date=r[0], tokens=r[1], requests=r[2])
        for r in daily_result.all()
    ]

    return AdminAnalyticsResponse(
        total_tokens=total_tokens,
        total_requests=total_requests,
        daily_breakdown=daily_breakdown,
    )


# ===================================================================
# DELETE /api/admin/sessions/{session_id}
# ===================================================================


@router.delete("/sessions/{session_id}", status_code=204)
async def hard_delete_session(
    session_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """
    Hard-delete a session and its cascaded messages to purge violations.

    The session row and all associated messages are permanently removed.
    """
    result = await db.execute(
        select(Session).where(Session.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    # Delete messages first, then session
    await db.execute(
        delete(Message).where(Message.session_id == session_id)
    )
    await db.delete(session)
    await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===================================================================
# POST /api/admin/maintenance/prune
# ===================================================================


@router.post("/maintenance/prune", response_model=PruneResponse)
async def prune_old_data(
    body: PruneRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> PruneResponse:
    """
    Hard-delete sessions and messages older than ``older_than_days``
    to free database disk space.
    """
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=body.older_than_days)

    # Find old session IDs
    old_sessions_result = await db.execute(
        select(Session.id).where(Session.created_at < cutoff)
    )
    old_session_ids = [row[0] for row in old_sessions_result.all()]

    if not old_session_ids:
        return PruneResponse(sessions_deleted=0, messages_deleted=0)

    # Count messages to be deleted
    msg_count_result = await db.execute(
        select(func.count(Message.id)).where(
            Message.session_id.in_(old_session_ids)
        )
    )
    messages_deleted = msg_count_result.scalar_one()

    # Delete messages then sessions
    await db.execute(
        delete(Message).where(Message.session_id.in_(old_session_ids))
    )
    await db.execute(
        delete(Session).where(Session.id.in_(old_session_ids))
    )
    await db.flush()

    return PruneResponse(
        sessions_deleted=len(old_session_ids),
        messages_deleted=messages_deleted,
    )
