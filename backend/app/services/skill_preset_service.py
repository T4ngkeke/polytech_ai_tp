"""
services/skill_preset_service.py — [v7.2] teacher skill-preset library.

A teacher maintains a private library of reusable instructor-style (`skill.md`)
presets. Applying one to a class **snapshot-copies** its content into the class's
`level=class` rule via ``rule_service.upsert_rule`` — the prompt-injection path is
unchanged (it still reads `rules`). Editing a preset afterwards does not
retro-change classes that already selected it (snapshot, not live reference).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Rule, RuleLevel, SkillPreset
from backend.app.services import rule_service


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
    preset.name = name
    preset.content = content
    await db.flush()
    await db.refresh(preset)
    return preset


async def delete_preset(
    db: AsyncSession, *, owner_teacher_id: uuid.UUID, preset_id: uuid.UUID
) -> None:
    preset = await _get_owned(db, owner_teacher_id=owner_teacher_id, preset_id=preset_id)
    await db.delete(preset)
    await db.flush()


async def apply_preset_to_class(
    db: AsyncSession, *, owner_teacher_id: uuid.UUID, class_id: uuid.UUID,
    preset_id: uuid.UUID,
) -> Rule:
    """Snapshot-copy a preset's content into the class's level=class skill rule."""
    preset = await _get_owned(db, owner_teacher_id=owner_teacher_id, preset_id=preset_id)
    return await rule_service.upsert_rule(
        db, level=RuleLevel.class_, target_id=class_id, rules_text=preset.content,
    )
