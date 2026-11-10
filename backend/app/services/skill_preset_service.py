"""
services/skill_preset_service.py — teacher skill-preset library.

A teacher keeps a private library of their own rules ("My rules"). The rule
lists of the teacher page add one with a click: the text is copied into a new
rule linked to it (rule_items.source 'm:<id>'). Editing a saved rule updates
the linked rules that still have its old text; deleting it unlinks them.
"""

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Class, RuleItem, SkillPreset


async def create_preset(
    db: AsyncSession, *, owner_teacher_id: uuid.UUID, name: str, content: str
) -> SkillPreset:
    preset = SkillPreset(owner_teacher_id=owner_teacher_id, name=name, content=content)
    db.add(preset)
    await db.flush()
    await db.refresh(preset)
    return preset


async def list_presets(
    db: AsyncSession, *, owner_teacher_id: uuid.UUID
) -> list[SkillPreset]:
    rows = await db.execute(
        select(SkillPreset)
        .where(SkillPreset.owner_teacher_id == owner_teacher_id)
        .order_by(SkillPreset.created_at)
    )
    return list(rows.scalars().all())


async def _get_owned(
    db: AsyncSession, *, owner_teacher_id: uuid.UUID, preset_id: uuid.UUID
) -> SkillPreset:
    preset = await db.get(SkillPreset, preset_id)
    if preset is None:
        raise LookupError("Skill preset not found")
    if preset.owner_teacher_id != owner_teacher_id:
        raise PermissionError("Not the owner of this skill preset")
    return preset


async def update_preset(
    db: AsyncSession, *, owner_teacher_id: uuid.UUID, preset_id: uuid.UUID,
    name: str, content: str,
) -> SkillPreset:
    preset = await _get_owned(db, owner_teacher_id=owner_teacher_id, preset_id=preset_id)
    old = preset.content
    preset.name = name
    preset.content = content
    await db.execute(update(RuleItem).where(
        RuleItem.source == f"m:{preset_id}", RuleItem.text == old,
        RuleItem.class_id.in_(select(Class.id).where(Class.teacher_id == owner_teacher_id)))
        .values(text=content.strip()).execution_options(synchronize_session=False))
    await db.flush()
    await db.refresh(preset)
    return preset


async def delete_preset(
    db: AsyncSession, *, owner_teacher_id: uuid.UUID, preset_id: uuid.UUID
) -> None:
    preset = await _get_owned(db, owner_teacher_id=owner_teacher_id, preset_id=preset_id)
    # The rules added from it stay in their lists, no longer linked.
    await db.execute(update(RuleItem).where(RuleItem.source == f"m:{preset_id}")
                     .values(source=None).execution_options(synchronize_session=False))
    await db.delete(preset)
    await db.flush()
