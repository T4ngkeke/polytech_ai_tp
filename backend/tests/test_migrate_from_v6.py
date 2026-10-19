"""
test_migrate_from_v6.py — v6 rows become current objects; every class is in exam mode;
conversations and usage come along. The script was run on a copy of the v6-beta
database on 2026-10-15 (accounts) and 2026-10-16 (with conversations).
"""

import uuid
from datetime import datetime, timezone

import pytest

from backend.app.models import Class, Message, Rule, RuleLevel, Session, User, UserRole
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
        'rules': [dict(id=(rule := uuid.uuid4()), level='class', target_id=cls, rules_text='Réponds en français.', is_active=True)],
        'sessions': [dict(id=(session := uuid.uuid4()), user_id=student, lab_id=lab, title='Tri', is_deleted=True,
                          created_at=now)],
        'messages': [dict(id=uuid.uuid4(), session_id=session, sender='user', content='Comment trier ?',
                          prompt_tokens=None, completion_tokens=None, total_tokens=None, created_at=now),
                     dict(id=(answer := uuid.uuid4()), session_id=session, sender='llm', content='Par insertion.',
                          prompt_tokens=10, completion_tokens=5, total_tokens=15, created_at=now)],
        'usage_stats': [dict(id=uuid.uuid4(), user_id=student, date=now.date(), tokens_used=15, request_count=1)],
    }
    db_session.add_all(rows_to_objects(data))
    await db_session.commit()
    prof = await db_session.get(User, teacher)
    assert (prof.role, prof.hashed_password, prof.daily_token_quota) == (UserRole.teacher, '$2b$12$hash', 10)
    copied = await db_session.get(Class, cls)
    assert copied.exam_mode is True and copied.invite_code == 'ABC123'
    assert (await db_session.get(Rule, rule)).level == RuleLevel.class_
    # A deleted conversation stays visible to the teacher; a v6 answer has no mode.
    kept = await db_session.get(Session, session)
    assert kept.is_deleted and not kept.is_test
    reply = await db_session.get(Message, answer)
    assert reply.sender.value == 'llm' and reply.answer_mode is None and reply.total_tokens == 15
