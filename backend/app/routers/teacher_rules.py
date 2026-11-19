"""Teacher routes for rule lists and skill presets."""

import contextlib
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import require_teacher
from backend.app.database import get_db
from backend.app.models import Class, ClassStudent, Lab, RuleItem, SkillPreset, User
from backend.app.schemas import (
    RuleItemCreate,
    RuleItemResponse,
    RuleItemUpdate,
    SkillPresetResponse,
    SkillPresetUpsertRequest,
)
from backend.app.services import rule_service, skill_preset_service

router = APIRouter(prefix="/api/teacher", tags=["teacher"])


async def _rule_class(
    db: AsyncSession, level: str, target_id: uuid.UUID, class_id: uuid.UUID | None, teacher: User
) -> uuid.UUID:
    """The class a rule target belongs to, when it is one of the teacher's.

    These rules go into the chat prompt, so a teacher may touch only targets in
    their own classes (403 otherwise). A student's rules belong to one class:
    class_id is required, and the student must be in that class.
    """
    owned = None
    if level == "class":
        owned = await db.scalar(select(Class.id).where(
            Class.id == target_id, Class.teacher_id == teacher.id, Class.is_deleted.is_(False)))
    elif level == "lab":
        owned = await db.scalar(select(Lab.class_id).join(Class, Lab.class_id == Class.id).where(
            Lab.id == target_id, Class.teacher_id == teacher.id, Class.is_deleted.is_(False)))
    elif level == "student" and class_id is not None:
        owned = await db.scalar(select(Class.id).join(ClassStudent, ClassStudent.class_id == Class.id).where(
            Class.id == class_id, Class.teacher_id == teacher.id, Class.is_deleted.is_(False),
            ClassStudent.student_id == target_id))
    if owned is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this rule target")
    return owned


async def _check_source(db: AsyncSession, source: str | None, teacher: User) -> str | None:
    """A rule's source must be a ready-made rule or one of this teacher's saved rules (422 otherwise)."""
    if source is None:
        return None
    kind, _, ref = source.partition(":")
    if kind == "b" and ref in rule_service.BUILT_IN_KEYS:
        return source
    if kind == "m":
        with contextlib.suppress(ValueError):
            if await db.scalar(select(SkillPreset.id).where(
                    SkillPreset.id == uuid.UUID(ref), SkillPreset.owner_teacher_id == teacher.id)):
                return source
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unknown_rule_source")


async def _owned_item(db: AsyncSession, item_id: uuid.UUID, teacher: User) -> RuleItem:
    item = await db.scalar(select(RuleItem).join(Class, Class.id == RuleItem.class_id).where(
        RuleItem.id == item_id, Class.teacher_id == teacher.id))
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return item


# ===================================================================
# Rules: a list per class, lab or student (rule_service)
# ===================================================================


@router.get("/rule-items", response_model=list[RuleItemResponse])
async def list_rule_items(
    level: Literal["class", "lab", "student"],
    target_id: uuid.UUID,
    class_id: uuid.UUID | None = None,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[RuleItemResponse]:
    """The rules of a class, a lab, or a student in one class (class_id), in order."""
    owned_class = await _rule_class(db, level, target_id, class_id, teacher)
    items = await rule_service.list_items(db, level=level, target_id=target_id, class_id=owned_class)
    return [RuleItemResponse.model_validate(i) for i in items]


@router.post("/rule-items", response_model=RuleItemResponse, status_code=201)
async def add_rule_item(
    body: RuleItemCreate,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> RuleItemResponse:
    """Add a rule at the end of the list (or at `position`). 409 too_many_rules past 30."""
    owned_class = await _rule_class(db, body.level, body.target_id, body.class_id, teacher)
    source = await _check_source(db, body.source, teacher)
    try:
        item = await rule_service.add_item(db, level=body.level, target_id=body.target_id,
                                           class_id=owned_class, text=body.text, is_active=body.is_active,
                                           source=source, position=body.position)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    await db.commit()
    return RuleItemResponse.model_validate(item)


@router.patch("/rule-items/{item_id}", response_model=RuleItemResponse)
async def update_rule_item(
    item_id: uuid.UUID,
    body: RuleItemUpdate,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> RuleItemResponse:
    """Change the text of a rule, switch it on or off, or change its source."""
    item = await _owned_item(db, item_id, teacher)
    extra = {}
    if "source" in body.model_fields_set:
        extra["source"] = await _check_source(db, body.source, teacher)
    item = await rule_service.update_item(db, item, text=body.text, is_active=body.is_active, **extra)
    await db.commit()
    return RuleItemResponse.model_validate(item)


@router.delete("/rule-items/{item_id}", status_code=204)
async def delete_rule_item(
    item_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    item = await _owned_item(db, item_id, teacher)
    await db.delete(item)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class RuleCheckRequest(BaseModel):
    level: Literal["class", "lab", "student"]
    target_id: uuid.UUID
    class_id: uuid.UUID | None = None


@router.post("/rule-items/check")
async def check_rule_items(
    body: RuleCheckRequest,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """Pairs of active rules in one list that contradict, read by the model
    (services/rule_check.py). positions are the numbers the page shows.
    A warning for the teacher, not stored; 503 rule_check_unavailable when
    the model cannot answer. Not charged to anyone's quota."""
    from backend.app.agent.model import GREEDY, ResearchModel
    from backend.app.services import failure_log, rule_check
    from backend.app.services.model_clients import get_llm_config
    from backend.app.services.model_routing import Endpoint

    owned_class = await _rule_class(db, body.level, body.target_id, body.class_id, teacher)
    items = await rule_service.list_items(db, level=body.level, target_id=body.target_id, class_id=owned_class)
    active = {n: item for n, item in enumerate(items, 1) if item.is_active}
    if len(active) < 2:
        return {"conflicts": []}
    config = await get_llm_config(db)
    agent = Endpoint(config["agent_base_url"], config["agent_api_key"], config["agent_model"])
    if not agent.model or not agent.base_url:
        raise HTTPException(503, "rule_check_unavailable")
    try:
        found = await rule_check.find_conflicts(ResearchModel(agent, sampling=GREEDY),
                                                {n: item.text for n, item in active.items()})
    except Exception as exc:  # noqa: BLE001 - only a warning is lost
        failure_log.record("rule_check", type(exc).__name__)
        raise HTTPException(503, "rule_check_unavailable") from None
    return {"conflicts": [{"positions": c["rules"], "rule_ids": [str(active[n].id) for n in c["rules"]],
                           "reason": c["reason"]} for c in found]}


@router.get("/rule-presets")
async def rule_presets(teacher: User = Depends(require_teacher)) -> list[dict]:
    """Ready-made rules to add with one click (rule_service.BUILT_IN)."""
    return rule_service.BUILT_IN


# ===================================================================
# Skill presets — a teacher's own rules to reuse (the rule lists add them)
# ===================================================================


@router.get("/skill-presets", response_model=list[SkillPresetResponse])
async def list_skill_presets(
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[SkillPresetResponse]:
    presets = await skill_preset_service.list_presets(db, owner_teacher_id=teacher.id)
    return [SkillPresetResponse.model_validate(p) for p in presets]


@router.post("/skill-presets", response_model=SkillPresetResponse, status_code=201)
async def create_skill_preset(
    body: SkillPresetUpsertRequest,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> SkillPresetResponse:
    preset = await skill_preset_service.create_preset(
        db, owner_teacher_id=teacher.id, name=body.name, content=body.content)
    return SkillPresetResponse.model_validate(preset)


@router.put("/skill-presets/{preset_id}", response_model=SkillPresetResponse)
async def update_skill_preset(
    preset_id: uuid.UUID,
    body: SkillPresetUpsertRequest,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> SkillPresetResponse:
    """Change a saved rule. The rules added from it whose text was not changed
    since follow the new text, in every class of the teacher."""
    try:
        preset = await skill_preset_service.update_preset(
            db, owner_teacher_id=teacher.id, preset_id=preset_id,
            name=body.name, content=body.content)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preset not found")
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not the owner")
    return SkillPresetResponse.model_validate(preset)


@router.delete("/skill-presets/{preset_id}", status_code=204)
async def delete_skill_preset(
    preset_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    """Delete a saved rule. Rules added from it stay in their lists, unlinked."""
    try:
        await skill_preset_service.delete_preset(
            db, owner_teacher_id=teacher.id, preset_id=preset_id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preset not found")
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not the owner")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
