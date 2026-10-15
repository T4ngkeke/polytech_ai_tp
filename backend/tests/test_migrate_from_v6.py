"""
test_migrate_from_v6.py — v6 rows become current objects; every class is in exam mode.
The script itself was run on a copy of the v6-beta database on 2026-10-15.
"""

import uuid
from datetime import datetime, timezone

import pytest

from backend.app.models import Class, RuleLevel, User, UserRole
from scripts.migrate_from_v6 import rows_to_objects


@pytest.mark.asyncio
async def test_v6_rows_keep_ids_and_hashes_and_classes_start_in_exam_mode(db_session):
    teacher, student, cls, lab = (uuid.uuid4() for _ in range(4))
    now = datetime.now(timezone.utc)
    data = {
        'users': [dict(id=teacher, username='prof', hashed_password='$2b$12$hash', role='teacher',
                       daily_token_quota=10, is_deleted=False),
                  dict(id=student, username='eleve', hashed_password='$2b$12$other', role='student',
                       daily_token_quota=20, is_deleted=False)],
        'classes': [dict(id=cls, name='Algo', teacher_id=teacher, invite_code='ABC123', is_deleted=False, created_at=now)],
        'class_students': [dict(class_id=cls, student_id=student, joined_at=now)],
        'labs': [dict(id=lab, class_id=cls, name='TP1', is_deleted=False, is_active=False, created_at=now)],
        'rules': [dict(id=uuid.uuid4(), level='class', target_id=cls, rules_text='Réponds en français.', is_active=True)],
    }
    db_session.add_all(rows_to_objects(data))
    await db_session.commit()
    prof = await db_session.get(User, teacher)
    assert (prof.role, prof.hashed_password, prof.daily_token_quota) == (UserRole.teacher, '$2b$12$hash', 10)
    copied = await db_session.get(Class, cls)
    assert copied.exam_mode is True and copied.invite_code == 'ABC123'
    assert rows_to_objects(data)[-1].level == RuleLevel.class_
