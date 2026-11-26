"""
routers/admin.py — Admin-only endpoints for Edu-LLM.

All DB/business logic is delegated to the services/ layer.
Admin bypasses ownership checks — no class/lab ownership is verified here.

Endpoints
---------
GET    /api/admin/users
POST   /api/admin/users
PUT    /api/admin/users/{user_id}/quota
POST   /api/admin/users/{user_id}/quota-refill
PUT    /api/admin/users/{user_id}/role
DELETE /api/admin/users/{user_id}
GET    /api/admin/llm/config
GET    /api/admin/llm/servers
PUT    /api/admin/llm/config
POST   /api/admin/users/import
GET    /api/admin/classes
GET    /api/admin/classes/{class_id}/labs
GET    /api/admin/classes/{class_id}/students
PUT    /api/admin/classes/{class_id}/transfer
DELETE /api/admin/classes/{class_id}
GET    /api/admin/documents
GET    /api/admin/labs
PUT    /api/admin/labs/{lab_id}
DELETE /api/admin/labs/{lab_id}
GET    /api/admin/analytics
GET    /api/admin/analytics/classes/{class_id}
GET    /api/admin/analytics/classes/{class_id}/details?lab_id=
DELETE /api/admin/sessions/{session_id}
POST   /api/admin/maintenance/prune
"""

import csv
import io
import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.auth import hash_password_async, require_admin
from backend.app.database import get_db
from backend.app.models import (
    Message,
    Session,
    SystemConfig,
    User,
    UserRole,
)
from backend.app.schemas import (
    AdminAnalyticsResponse,
    AdminClassResponse,
    AdminDocumentResponse,
    ClassResponse,
    CSVImportPreviewResponse,
    DailyUsage,
    LabResponse,
    LLMConfigRequest,
    LLMConfigResponse,
    PruneRequest,
    PruneResponse,
    ServerCheckRequest,
    TransferClassRequest,
    UpdateQuotaRequest,
    UpdateRoleRequest,
    UserCreateRequest,
    UserResponse,
)
from backend.app.services import class_service, document_service, feature_service, lab_service, analytics_service, model_servers, quota_service
from backend.app.services.model_routing import resolve_model_routing
from backend.app.services.research_service import DEFAULT_LIMITS, LIMIT_KEYS, limit_value

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
    db: AsyncSession = Depends(get_db, scope="function"),
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
    db: AsyncSession = Depends(get_db, scope="function"),
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
        hashed_password=await hash_password_async(body.password),
        role=body.role,
        daily_token_quota=body.daily_token_quota,
        weekly_token_quota=body.weekly_token_quota,
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
    db: AsyncSession = Depends(get_db, scope="function"),
) -> UserResponse:
    """Update the daily (and, when given, weekly) token quota of a user."""
    user = await _get_active_user_or_404(db, user_id)
    user.daily_token_quota = body.daily_token_quota
    if body.weekly_token_quota is not None:
        user.weekly_token_quota = body.weekly_token_quota
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return UserResponse.model_validate(user)


# ===================================================================
# POST /api/admin/users/{user_id}/quota-refill
# ===================================================================


@router.post("/users/{user_id}/quota-refill")
async def refill_quota(
    user_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """Give any user a full day and week again; usage is kept (quota_service)."""
    user = await _get_active_user_or_404(db, user_id)
    usage = await quota_service.refill(db, student=user, by=admin.id)
    await db.commit()
    return {"used": usage.used, "limit": usage.limit,
            "week_used": usage.week_used, "week_limit": usage.week_limit}


# ===================================================================
# PUT /api/admin/users/{user_id}/role
# ===================================================================


@router.put("/users/{user_id}/role", response_model=UserResponse)
async def update_role(
    user_id: uuid.UUID,
    body: UpdateRoleRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> UserResponse:
    """Change a user's role. An admin cannot change their own role (400
    cannot_change_own_role): with the self-delete rule, the admin who acts
    always stays, so there is always one admin."""
    user = await _get_active_user_or_404(db, user_id)
    if body.role != user.role and user.id == _admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot_change_own_role")
    user.role = body.role
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return UserResponse.model_validate(user)


# ===================================================================
# PUT /api/admin/users/{user_id}/password  (admin reset)
# ===================================================================


@router.put("/users/{user_id}/password", response_model=UserResponse)
async def reset_user_password(
    user_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> UserResponse:
    """Reset a user's password to settings.RESET_PASSWORD (123456). The user
    must choose a new one at the next sign-in (must_change_password): until
    then only /api/auth/me and /api/auth/change-password answer."""
    from backend.app.config import settings

    user = await _get_active_user_or_404(db, user_id)
    user.hashed_password = await hash_password_async(settings.RESET_PASSWORD)
    user.must_change_password = True
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
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    """Soft-delete a user. An admin cannot delete their own account (400
    cannot_delete_self): there is always one admin left."""
    user = await _get_active_user_or_404(db, user_id, detail="User not found or already deleted")
    if user.id == _admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot_delete_self")
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


# Map LLMConfig request/response field names → SystemConfig keys.
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
    "rerank_enabled": "RERANK_ENABLED",  # on / off; off keeps RERANK_URL
    # Agent slot.
    "agent_base_url": "AGENT_BASE_URL",
    "agent_api_key": "AGENT_API_KEY",
    "agent_model": "AGENT_MODEL",
    # VLM slot (garbled-formula transcription; empty model = off).
    "vlm_base_url": "VLM_BASE_URL",
    "vlm_api_key": "VLM_API_KEY",
    "vlm_model": "VLM_MODEL",
    # MinerU server of the PDF worker; empty = the worker's PDF_MINERU_SERVER_URL.
    "mineru_server_url": "PDF_MINERU_SERVER_URL",
    "mineru_api_key": "PDF_MINERU_API_KEY",  # empty = the main model key
    # Picture captions at ingest, by the VLM slot (documentation/PICTURE_CAPTIONS.md).
    "picture_captions": "PICTURE_CAPTIONS",
    "picture_caption_priority": "PICTURE_CAPTION_PRIORITY",
    "picture_caption_budget_seconds": "PICTURE_CAPTION_BUDGET_SECONDS",
    # Research agent limits (see research_service.ResearchLimits).
    "agent_max_tool_calls": "AGENT_MAX_TOOL_CALLS",
    "agent_max_visual_calls": "AGENT_MAX_VISUAL_CALLS",
    "agent_research_seconds": "AGENT_RESEARCH_SECONDS",
    "agent_total_seconds": "AGENT_TOTAL_SECONDS",
    "agent_call_seconds": "AGENT_CALL_SECONDS",
    "agent_max_input_tokens": "AGENT_MAX_INPUT_TOKENS",
    "history_context_percent": "HISTORY_CONTEXT_PERCENT",
}


# ===================================================================
# GET /api/admin/llm/config
# ===================================================================


@router.get("/llm/config", response_model=LLMConfigResponse)
async def get_llm_config(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
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
    # Token weights: reuse the single source of truth for parsing + defaults.
    routing = resolve_model_routing(configs)
    payload["token_alpha"] = routing.token_alpha
    payload["token_beta"] = routing.token_beta
    # What the map can show but not change: the deployment's feature flags and
    # the MinerU server the worker uses when the field is empty.
    payload["features"] = {
        "research_agent": settings.AGENTIC_SEARCH_ENABLED,
        "structured_documents": settings.STRUCTURED_INGEST_ENABLED,
        "pdf_documents": settings.PDF_STRUCTURED_INGEST_ENABLED,
    }
    payload["mineru_default_url"] = os.environ.get("PDF_MINERU_SERVER_URL", "")
    payload["research_limits"] = {
        field: {"default": DEFAULT_LIMITS[LIMIT_KEYS[key][0]],
                "min": LIMIT_KEYS[key][1], "max": LIMIT_KEYS[key][2]}
        for field, key in _LLM_CONFIG_STR_FIELDS.items() if key in LIMIT_KEYS}
    return LLMConfigResponse(**payload)


@router.get("/llm/servers")
async def get_model_servers(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """For each slot of the routing map: does its server answer, does it
    serve the configured model name, and that model's context length."""
    configs = {row.key: row.value for row in (await db.execute(select(SystemConfig))).scalars().all()}
    await db.rollback()  # no transaction is held while the servers answer
    return {"servers": await model_servers.server_status(resolve_model_routing(configs))}


@router.post("/llm/servers/check")
async def check_model_server(
    body: ServerCheckRequest,
    _admin: User = Depends(require_admin),
) -> dict:
    """Test one server with the values on the page, before they are saved:
    does it answer, does it accept the key, does it serve the model."""
    return await model_servers.check_server(body.url.strip(), body.model.strip(), body.api_key.strip())


# ===================================================================
# PUT /api/admin/llm/config
# ===================================================================


def _check_caption_fields(body) -> None:
    from backend.worker import picture_captions as pc
    for field in ('picture_captions', 'rerank_enabled'):
        if (getattr(body, field) or '') not in ('', 'on', 'off'):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{field} must be on or off")
    for field, (low, high) in (('picture_caption_priority', pc.PRIORITY_RANGE),
                               ('picture_caption_budget_seconds', pc.BUDGET_RANGE)):
        value = getattr(body, field)
        if value and pc.whole_number(value, low, high) is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"{field} must be a whole number from {low} to {high}")


@router.put("/llm/config", response_model=LLMConfigResponse)
async def update_llm_config(
    body: LLMConfigRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> LLMConfigResponse:
    """Zero-downtime upsert of the model-routing table.

    Only fields explicitly provided (non-None) are written, so a legacy 3-field
    PUT (base_url/api_key/model) never wipes the routing config.
    """
    for field, key in _LLM_CONFIG_STR_FIELDS.items():
        value = getattr(body, field)
        if key in LIMIT_KEYS and value and limit_value(key, value) is None:
            _, low, high = LIMIT_KEYS[key]
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{field} must be a whole number from {low} to {high}",
            )
    _check_caption_fields(body)
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
    db: AsyncSession = Depends(get_db, scope="function"),
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
    seen_in_file: set[str] = set()

    for i, row in enumerate(rows, start=2):
        # DictReader yields None for missing cells on short rows — coerce before strip.
        username = (row.get("username") or "").strip()
        password = (row.get("password") or "").strip()
        role_str = (row.get("role") or "student").strip().lower()

        if not username or not password:
            conflicts.append({"row": i, "username": username, "reason": "Missing username or password"})
            continue
        if username in seen_in_file:
            conflicts.append({"row": i, "username": username, "reason": "Duplicate username in file"})
            continue
        seen_in_file.add(username)
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
            db.add(User(username=entry["username"], hashed_password=await hash_password_async(entry["password"]), role=entry["role"]))
        response.created = len(to_create)

        for entry in to_update:
            res = await db.execute(select(User).where(User.username == entry["username"]))
            user = res.scalar_one()
            user.hashed_password = await hash_password_async(entry["password"])
            db.add(user)
        response.updated = len(to_update)
        await db.flush()

    return response


# ===================================================================
# GET /api/admin/classes
# ===================================================================


@router.get("/classes", response_model=list[AdminClassResponse])
async def list_all_classes(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[AdminClassResponse]:
    """List all non-deleted classes system-wide, with the teacher who owns
    each one and a few counts."""
    classes = await class_service.list_all_classes(db, skip=skip, limit=limit)
    owners = {u.id: u for u in (await db.scalars(
        select(User).where(User.id.in_({c.teacher_id for c in classes})))).all()} if classes else {}
    counts = await analytics_service.get_class_overview(db, [c.id for c in classes])
    out = []
    for c in classes:
        owner = owners.get(c.teacher_id)
        out.append(AdminClassResponse.model_validate(c).model_copy(update={
            "teacher_username": owner.username if owner else None,
            "teacher_ok": owner is not None and not owner.is_deleted
                          and owner.role in (UserRole.teacher, UserRole.admin),
            **counts[c.id],
        }))
    return out


# ===================================================================
# PUT /api/admin/classes/{class_id}/transfer
# ===================================================================


@router.put("/classes/{class_id}/transfer", response_model=ClassResponse)
async def transfer_class(
    class_id: uuid.UUID,
    body: TransferClassRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> ClassResponse:
    """Force-change the teacher_id of any class."""
    cls = await class_service.get_class_by_id(db, class_id)
    cls = await class_service.transfer_class(db, cls, new_teacher_id=body.teacher_id)
    return ClassResponse.model_validate(cls)


# ===================================================================
# GET /api/admin/documents
# ===================================================================


@router.get("/documents", response_model=list[AdminDocumentResponse])
async def list_all_documents(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[AdminDocumentResponse]:
    """Every course document of every class, newest upload first, with its
    class, lab, uploader and processing state: did each upload work?"""
    return [AdminDocumentResponse(**row) for row in await document_service.list_all_documents(db)]


# ===================================================================
# GET /api/admin/labs
# ===================================================================


@router.get("/labs", response_model=list[LabResponse])
async def list_all_labs(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[LabResponse]:
    """List all non-deleted labs system-wide."""
    labs = await lab_service.list_all_labs(db, skip=skip, limit=limit)
    return await feature_service.fill_effective(db, [LabResponse.model_validate(l) for l in labs])


# ===================================================================
# GET /api/admin/analytics
# ===================================================================


@router.get("/analytics", response_model=AdminAnalyticsResponse)
async def get_global_analytics(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> AdminAnalyticsResponse:
    """Global token usage dashboard with 30-day daily breakdown."""
    result = await analytics_service.get_global_analytics(db)
    return AdminAnalyticsResponse(
        total_tokens=result.total_tokens,
        total_requests=result.total_requests,
        active_users_7d=result.active_users_7d,
        daily_breakdown=[
            DailyUsage(date=d["date"], tokens=d["tokens"], requests=d["requests"])
            for d in result.daily_breakdown
        ],
    )


# ===================================================================
# DELETE /api/admin/sessions/{session_id}
# ===================================================================


@router.delete("/sessions/{session_id}", status_code=204)
async def hard_delete_session(
    session_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
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
    db: AsyncSession = Depends(get_db, scope="function"),
) -> PruneResponse:
    """Hard-delete sessions and messages older than `older_than_days`; with
    dry_run, only count them."""
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=body.older_than_days)

    old_sessions_result = await db.execute(select(Session.id).where(Session.created_at < cutoff))
    old_session_ids = [row[0] for row in old_sessions_result.all()]

    messages_deleted = 0
    if old_session_ids:
        msg_count_result = await db.execute(
            select(func.count(Message.id)).where(Message.session_id.in_(old_session_ids))
        )
        messages_deleted = msg_count_result.scalar_one()
        if not body.dry_run:
            await db.execute(delete(Message).where(Message.session_id.in_(old_session_ids)))
            await db.execute(delete(Session).where(Session.id.in_(old_session_ids)))

    await db.flush()

    return PruneResponse(
        dry_run=body.dry_run,
        sessions_deleted=len(old_session_ids),
        messages_deleted=messages_deleted,
    )
