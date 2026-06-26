"""
routers/admin.py — Admin-only endpoints for Edu-LLM v7.

All DB/business logic is delegated to the services/ layer.
Admin bypasses ownership checks — no class/lab ownership is verified here.

Endpoints
---------
GET    /api/admin/users
POST   /api/admin/users
PUT    /api/admin/users/{user_id}/quota
PUT    /api/admin/users/{user_id}/role
DELETE /api/admin/users/{user_id}
GET    /api/admin/llm/config
PUT    /api/admin/llm/config
POST   /api/admin/users/import
GET    /api/admin/classes
GET    /api/admin/classes/{class_id}/labs
GET    /api/admin/classes/{class_id}/students
PUT    /api/admin/classes/{class_id}/transfer
DELETE /api/admin/classes/{class_id}
GET    /api/admin/labs
DELETE /api/admin/labs/{lab_id}
GET    /api/admin/analytics
GET    /api/admin/analytics/classes/{class_id}
DELETE /api/admin/sessions/{session_id}
POST   /api/admin/maintenance/prune
"""

import csv
import io
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import hash_password, require_admin
from backend.app.database import get_db
from backend.app.models import Class, Message, Session, SystemConfig, User, UserRole
from backend.app.schemas import (
    AdminAnalyticsResponse,
    AdminResetPasswordRequest,
    ClassAnalyticsResponse,
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
from backend.app.services import class_service, lab_service, analytics_service

router = APIRouter(prefix="/api/admin", tags=["admin"])


async def _get_active_user_or_404(
    db: AsyncSession, user_id: uuid.UUID, detail: str = "User not found"
) -> User:
    """Fetch a non-deleted user by id or raise 404."""
    result = await db.execute(
        select(User).where(User.id == user_id, User.is_deleted.is_(False))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return user


# ===================================================================
# GET /api/admin/users
# ===================================================================


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[UserResponse]:
    """Return all non-deleted users."""
    result = await db.execute(select(User).where(User.is_deleted.is_(False)))
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
    """Create a new platform user. Returns 409 if username already exists."""
    existing = await db.execute(select(User).where(User.username == body.username))
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
    """Update the daily token quota for a user."""
    user = await _get_active_user_or_404(db, user_id)
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
    """Change a user's role."""
    user = await _get_active_user_or_404(db, user_id)
    user.role = body.role
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return UserResponse.model_validate(user)


# ===================================================================
# PUT /api/admin/users/{user_id}/password  ([v7.2] admin reset)
# ===================================================================


@router.put("/users/{user_id}/password", response_model=UserResponse)
async def reset_user_password(
    user_id: uuid.UUID,
    body: AdminResetPasswordRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Admin reset of a user's password — no old password required."""
    user = await _get_active_user_or_404(db, user_id)
    user.hashed_password = hash_password(body.new_password)
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
    """Soft-delete a user."""
    user = await _get_active_user_or_404(db, user_id, detail="User not found or already deleted")
    user.is_deleted = True
    db.add(user)
    await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===================================================================
# LLM Config helpers
# ===================================================================


async def _upsert_config(db: AsyncSession, key: str, value: str) -> None:
    result = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
    config = result.scalar_one_or_none()
    if config is None:
        config = SystemConfig(key=key, value=value)
        db.add(config)
    else:
        config.value = value
        db.add(config)


# [v7.2] Map LLMConfig request/response field names → SystemConfig keys.
# String fields (endpoints/models/knobs); token weights are handled separately.
_LLM_CONFIG_STR_FIELDS: dict[str, str] = {
    "base_url": "LLM_BASE_URL",
    "api_key": "LLM_API_KEY",
    "model": "LLM_MODEL",
    "embedding_url": "EMBEDDING_URL",
    "embedding_api_key": "EMBEDDING_API_KEY",
    "embedding_model": "EMBEDDING_MODEL",
    "rerank_url": "RERANK_URL",
    "rerank_api_key": "RERANK_API_KEY",
    "rerank_model": "RERANK_MODEL",
    "ingest_base_url": "INGEST_BASE_URL",
    "ingest_api_key": "INGEST_API_KEY",
    "ingest_model": "INGEST_MODEL",
    "router_base_url": "ROUTER_BASE_URL",
    "router_api_key": "ROUTER_API_KEY",
    "router_model": "ROUTER_MODEL",
    "rag_max_retries": "RAG_MAX_RETRIES",
    "router_knn_threshold": "ROUTER_KNN_THRESHOLD",
}


def _float_config(configs: dict[str, str], key: str, default: float) -> float:
    try:
        return float(configs[key])
    except (KeyError, TypeError, ValueError):
        return default


# ===================================================================
# GET /api/admin/llm/config
# ===================================================================


@router.get("/llm/config", response_model=LLMConfigResponse)
async def get_llm_config(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> LLMConfigResponse:
    """Read the full LLM/model-routing config from the SystemConfig table.

    Raw stored values are returned — an empty string means the role inherits the
    main LLM (see ``model_routing.resolve_model_routing``).
    """
    result = await db.execute(select(SystemConfig))
    configs = {row.key: row.value for row in result.scalars().all()}
    payload = {
        field: configs.get(key, "") for field, key in _LLM_CONFIG_STR_FIELDS.items()
    }
    payload["token_alpha"] = _float_config(configs, "TOKEN_ALPHA", 0.2)
    payload["token_beta"] = _float_config(configs, "TOKEN_BETA", 1.0)
    return LLMConfigResponse(**payload)


# ===================================================================
# PUT /api/admin/llm/config
# ===================================================================


@router.put("/llm/config", response_model=LLMConfigResponse)
async def update_llm_config(
    body: LLMConfigRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> LLMConfigResponse:
    """Zero-downtime upsert of the model-routing table.

    Only fields explicitly provided (non-None) are written, so a legacy 3-field
    PUT (base_url/api_key/model) never wipes the routing config.
    """
    for field, key in _LLM_CONFIG_STR_FIELDS.items():
        value = getattr(body, field)
        if value is not None:
            await _upsert_config(db, key, value)
    if body.token_alpha is not None:
        await _upsert_config(db, "TOKEN_ALPHA", str(body.token_alpha))
    if body.token_beta is not None:
        await _upsert_config(db, "TOKEN_BETA", str(body.token_beta))
    await db.flush()
    return await get_llm_config(_admin=_admin, db=db)


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
    """CSV bulk import: username, password, role (optional). Dry-run by default."""
    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)

    if not rows:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSV file is empty or has no data rows")

    required_cols = {"username", "password"}
    if rows[0].keys() and not required_cols.issubset(set(rows[0].keys())):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"CSV must contain columns: {', '.join(required_cols)}. Found: {', '.join(rows[0].keys())}",
        )

    existing_result = await db.execute(select(User.username))
    existing_usernames = {row[0] for row in existing_result.all()}

    to_create = []
    to_update = []
    conflicts = []

    for i, row in enumerate(rows, start=2):
        username = row.get("username", "").strip()
        password = row.get("password", "").strip()
        role_str = row.get("role", "student").strip().lower()

        if not username or not password:
            conflicts.append({"row": i, "username": username, "reason": "Missing username or password"})
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
        for entry in to_create:
            db.add(User(username=entry["username"], hashed_password=hash_password(entry["password"]), role=entry["role"]))
        response.created = len(to_create)

        for entry in to_update:
            res = await db.execute(select(User).where(User.username == entry["username"]))
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
    """List all non-deleted classes system-wide."""
    classes = await class_service.list_all_classes(db, skip=skip, limit=limit)
    return [ClassResponse.model_validate(c) for c in classes]


# ===================================================================
# GET /api/admin/classes/{class_id}/labs
# ===================================================================


@router.get("/classes/{class_id}/labs", response_model=list[LabResponse])
async def list_class_labs(
    class_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[LabResponse]:
    """List all labs in a class (no ownership check)."""
    await class_service.get_class_by_id(db, class_id)  # 404 if not found
    labs = await lab_service.list_labs_for_class(db, class_id=class_id)
    return [LabResponse.model_validate(l) for l in labs]


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
    await class_service.get_class_by_id(db, class_id)  # 404 if not found
    students = await class_service.list_students_in_class(db, class_id=class_id)
    return [UserResponse.model_validate(s) for s in students]


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
    """Force-change the teacher_id of any class."""
    cls = await class_service.get_class_by_id(db, class_id)
    cls = await class_service.transfer_class(db, cls, new_teacher_id=body.teacher_id)
    return ClassResponse.model_validate(cls)


# ===================================================================
# DELETE /api/admin/classes/{class_id}
# ===================================================================


@router.delete("/classes/{class_id}", status_code=204)
async def force_delete_class(
    class_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Force soft-delete any class system-wide (no ownership check)."""
    cls = await class_service.get_class_by_id(db, class_id)
    await class_service.soft_delete_class(db, cls)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    labs = await lab_service.list_all_labs(db, skip=skip, limit=limit)
    return [LabResponse.model_validate(l) for l in labs]


# ===================================================================
# DELETE /api/admin/labs/{lab_id}
# ===================================================================


@router.delete("/labs/{lab_id}", status_code=204)
async def force_delete_lab(
    lab_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Force soft-delete any lab system-wide (no ownership check)."""
    lab = await lab_service.get_lab_by_id(db, lab_id)
    await lab_service.soft_delete_lab(db, lab)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===================================================================
# GET /api/admin/analytics
# ===================================================================


@router.get("/analytics", response_model=AdminAnalyticsResponse)
async def get_global_analytics(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminAnalyticsResponse:
    """Global token usage dashboard with 30-day daily breakdown."""
    result = await analytics_service.get_global_analytics(db)
    return AdminAnalyticsResponse(
        total_tokens=result.total_tokens,
        total_requests=result.total_requests,
        daily_breakdown=[
            DailyUsage(date=d["date"], tokens=d["tokens"], requests=d["requests"])
            for d in result.daily_breakdown
        ],
    )


# ===================================================================
# GET /api/admin/analytics/classes/{class_id}
# ===================================================================


@router.get("/analytics/classes/{class_id}", response_model=ClassAnalyticsResponse)
async def get_class_analytics(
    class_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ClassAnalyticsResponse:
    """Per-class hierarchical analytics (class→lab→student). No ownership check."""
    cls = await class_service.get_class_by_id(db, class_id)
    result = await analytics_service.get_class_analytics(db, class_id=class_id, class_name=cls.name)
    return ClassAnalyticsResponse.from_analytics(result)


# ===================================================================
# DELETE /api/admin/sessions/{session_id}
# ===================================================================


@router.delete("/sessions/{session_id}", status_code=204)
async def hard_delete_session(
    session_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Hard-delete a session and all its messages to purge inappropriate content."""
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    await db.execute(delete(Message).where(Message.session_id == session_id))
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
    """Hard-delete sessions and messages older than `older_than_days`."""
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=body.older_than_days)
    old_sessions_result = await db.execute(select(Session.id).where(Session.created_at < cutoff))
    old_session_ids = [row[0] for row in old_sessions_result.all()]

    if not old_session_ids:
        return PruneResponse(sessions_deleted=0, messages_deleted=0)

    msg_count_result = await db.execute(
        select(func.count(Message.id)).where(Message.session_id.in_(old_session_ids))
    )
    messages_deleted = msg_count_result.scalar_one()

    await db.execute(delete(Message).where(Message.session_id.in_(old_session_ids)))
    await db.execute(delete(Session).where(Session.id.in_(old_session_ids)))
    await db.flush()

    return PruneResponse(sessions_deleted=len(old_session_ids), messages_deleted=messages_deleted)
