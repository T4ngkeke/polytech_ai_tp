"""
test_skill_preset_service.py — [v7.2] teacher skill-preset library + apply-to-class.

A teacher keeps a private library of reusable instructor-style presets. Applying
one to a class *snapshot-copies* its content into the class's level=class rule —
the prompt-injection path is unchanged. Editing a preset afterwards must NOT
retro-change classes that already selected it.
"""

import uuid

import pytest

from backend.app.models import UserRole
from backend.app.services import rule_service, skill_preset_service
from backend.tests.conftest import make_user

pytestmark = pytest.mark.asyncio


async def _teacher(db):
    t = make_user(role=UserRole.teacher)
    db.add(t)
    await db.flush()
    return t


async def test_create_and_list_presets_are_owner_scoped(db_session):
    t1 = await _teacher(db_session)
    t2 = await _teacher(db_session)
    await skill_preset_service.create_preset(db_session, owner_teacher_id=t1.id,
                                             name="Strict", content="Be strict.")
    await skill_preset_service.create_preset(db_session, owner_teacher_id=t2.id,
                                             name="Friendly", content="Be kind.")

    t1_presets = await skill_preset_service.list_presets(db_session, owner_teacher_id=t1.id)
    assert [p.name for p in t1_presets] == ["Strict"]


async def test_apply_preset_snapshots_into_class_rule(db_session):
    t = await _teacher(db_session)
    class_id = uuid.uuid4()
    preset = await skill_preset_service.create_preset(
        db_session, owner_teacher_id=t.id, name="Socratic", content="Guide, don't tell.")

    await skill_preset_service.apply_preset_to_class(
        db_session, owner_teacher_id=t.id, class_id=class_id, preset_id=preset.id)

    texts = await rule_service.get_active_rule_texts(db_session, class_id, None, uuid.uuid4())
    assert texts["class_rules"] == "Guide, don't tell."


async def test_editing_preset_does_not_retro_change_applied_class(db_session):
    t = await _teacher(db_session)
    class_id = uuid.uuid4()
    preset = await skill_preset_service.create_preset(
        db_session, owner_teacher_id=t.id, name="v1", content="ORIGINAL")
    await skill_preset_service.apply_preset_to_class(
        db_session, owner_teacher_id=t.id, class_id=class_id, preset_id=preset.id)

    await skill_preset_service.update_preset(
        db_session, owner_teacher_id=t.id, preset_id=preset.id, name="v2", content="CHANGED")

    # Snapshot semantics: the class rule keeps the ORIGINAL content.
    texts = await rule_service.get_active_rule_texts(db_session, class_id, None, uuid.uuid4())
    assert texts["class_rules"] == "ORIGINAL"


async def test_apply_other_teachers_preset_is_rejected(db_session):
    owner = await _teacher(db_session)
    other = await _teacher(db_session)
    preset = await skill_preset_service.create_preset(
        db_session, owner_teacher_id=owner.id, name="P", content="X")

    with pytest.raises(PermissionError):
        await skill_preset_service.apply_preset_to_class(
            db_session, owner_teacher_id=other.id, class_id=uuid.uuid4(), preset_id=preset.id)
