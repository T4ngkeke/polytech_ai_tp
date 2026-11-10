"""
test_skill_preset_service.py — a teacher's own presets, and the rule lists
that go into the prompt (rule_service).
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


async def test_rules_reach_the_prompt_as_numbered_active_lists(db_session):
    from backend.app.models import Class
    t = await _teacher(db_session)
    cls = Class(name="C", teacher_id=t.id, invite_code="RULE01")
    db_session.add(cls)
    await db_session.flush()
    student = uuid.uuid4()
    await rule_service.add_item(db_session, level="class", target_id=cls.id, class_id=cls.id, text=" Guide. ")
    off = await rule_service.add_item(db_session, level="class", target_id=cls.id, class_id=cls.id, text="Off.")
    await rule_service.add_item(db_session, level="class", target_id=cls.id, class_id=cls.id, text="Short.")
    await rule_service.update_item(db_session, off, is_active=False)
    texts = await rule_service.get_active_rule_texts(db_session, cls.id, None, student)
    assert texts == {"class_rules": "1. Guide.\n2. Short.", "lab_rules": None, "student_rules": None}
