"""export_service.py — a user's own conversations as one file (JSON or Markdown).

Students keep a copy of their work, and may take their data with them (GDPR).
Only the user's own visible conversations: not the ones they deleted, not
teacher test sessions. Citation markers such as [E1] stay in the answer text.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy import select

from ..models import Class, Lab, Message, SenderType, Session


async def conversations(db, user_id: uuid.UUID) -> list[dict]:
    """The user's visible conversations, oldest first, each with its messages."""
    sessions = (await db.execute(
        select(Session, Lab.name, Class.name)
        .outerjoin(Lab, Lab.id == Session.lab_id)
        .outerjoin(Class, Class.id == Lab.class_id)
        .where(Session.user_id == user_id, Session.is_deleted.is_(False), Session.is_test.is_(False))
        .order_by(Session.created_at))).all()
    ids = [session.id for session, _, _ in sessions]
    messages: dict[uuid.UUID, list[Message]] = {i: [] for i in ids}
    if ids:
        rows = (await db.scalars(select(Message).where(Message.session_id.in_(ids))
                                 .order_by(Message.created_at))).all()
        for message in rows:
            messages[message.session_id].append(message)
    return [{
        'title': session.title,
        'class': class_name,
        'lab': lab_name,
        'created_at': session.created_at.isoformat(),
        'pinned': session.pinned,
        'messages': [{'role': 'student' if m.sender == SenderType.user else 'assistant',
                      'content': m.content, 'created_at': m.created_at.isoformat()}
                     for m in messages[session.id]],
    } for session, lab_name, class_name in sessions]


def as_json(username: str, convs: list[dict], now: datetime) -> str:
    return json.dumps({'user': username, 'exported_at': now.isoformat(), 'conversations': convs},
                      ensure_ascii=False, indent=2)


def as_markdown(username: str, convs: list[dict], now: datetime) -> str:
    lines = [f'# Edu-LLM — {username}', '', f'Exported {now:%Y-%m-%d %H:%M} UTC · {len(convs)} conversations', '']
    for conv in convs:
        where = ' › '.join(part for part in (conv['class'], conv['lab']) if part)
        lines += [f"## {conv['title'] or 'Untitled'}", '', f"{where} · {conv['created_at'][:10]}", '']
        for message in conv['messages']:
            who = 'Student' if message['role'] == 'student' else 'Edu-LLM'
            lines += [f"**{who}** ({message['created_at'][11:16]})", '', message['content'], '']
    return '\n'.join(lines)
