"""search_service.py — find words in conversations.

- A student searches their own conversations: titles and messages. Deleted
  conversations and teacher test sessions are left out. One hit per
  conversation (its latest matching message), newest first.
- A teacher searches the questions of a lab's students (the audit). Like the
  audit, conversations a student deleted are included and marked.

The match is case-insensitive (ILIKE) on the plain text: the data is small,
so no search index is needed. Accents count ("recursivite" does not find
"récursivité").
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select

from ..models import AgentRun, Class, Lab, Message, MessageCitation, SenderType, Session, User

SNIPPET_CHARS = 140
MESSAGE_SCAN_LIMIT = 500


def _pattern(query: str) -> str:
    """The ILIKE pattern of a query, its own % and _ taken literally."""
    escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'%{escaped}%'


def snippet(text: str, query: str, width: int = SNIPPET_CHARS) -> str:
    """A short piece of text around the first match, on one line."""
    flat = ' '.join((text or '').split())
    at = flat.lower().find(query.lower())
    if at < 0:
        return flat[:width] + ('…' if len(flat) > width else '')
    start = max(0, at - width // 3)
    end = min(len(flat), start + width)
    return ('…' if start > 0 else '') + flat[start:end] + ('…' if end < len(flat) else '')


async def student_search(db, user_id: uuid.UUID, query: str, limit: int = 20) -> list[dict]:
    """The user's conversations whose title or messages contain the query."""
    pattern = _pattern(query)
    visible = (Session.user_id == user_id, Session.is_deleted.is_(False), Session.is_test.is_(False))
    hits = (await db.execute(
        select(Message.id, Message.session_id, Message.content, Message.sender, Message.created_at)
        .join(Session, Session.id == Message.session_id)
        .where(*visible, Message.content.ilike(pattern, escape='\\'))
        .order_by(Message.created_at.desc()).limit(MESSAGE_SCAN_LIMIT))).all()
    best: dict[uuid.UUID, tuple] = {}
    for hit in hits:                       # newest first: keep the first per session
        best.setdefault(hit.session_id, hit)
    titled = (await db.scalars(select(Session.id).where(
        *visible, Session.title.ilike(pattern, escape='\\')))).all()
    ids = set(best) | set(titled)
    if not ids:
        return []
    sessions = (await db.execute(
        select(Session, Lab.name, Class.name)
        .outerjoin(Lab, Lab.id == Session.lab_id).outerjoin(Class, Class.id == Lab.class_id)
        .where(Session.id.in_(ids)))).all()
    out = []
    for session, lab_name, class_name in sessions:
        hit = best.get(session.id)
        out.append({
            'session_id': session.id, 'title': session.title, 'lab_id': session.lab_id,
            'lab_name': lab_name, 'class_name': class_name, 'pinned': session.pinned,
            'message_id': hit.id if hit else None,
            'role': ('student' if hit.sender == SenderType.user else 'assistant') if hit else None,
            'snippet': snippet(hit.content, query) if hit else None,
            'matched_at': hit.created_at if hit else session.created_at,
        })
    out.sort(key=lambda item: item['matched_at'], reverse=True)
    return out[:limit]


async def lab_question_search(db, lab_id: uuid.UUID, query: str, limit: int = 50) -> dict:
    """Students' questions in a lab that contain the query, newest first, with
    how each was answered (the answer mode, and whether it cites the course)."""
    where = (Session.lab_id == lab_id, Session.is_test.is_(False),
             Message.sender == SenderType.user, Message.content.ilike(_pattern(query), escape='\\'))
    total, students = (await db.execute(
        select(func.count(), func.count(func.distinct(Session.user_id))).select_from(Message)
        .join(Session, Session.id == Message.session_id).where(*where))).one()
    rows = (await db.execute(
        select(Message.id, Message.content, Message.created_at, Session.id, Session.is_deleted,
               User.id, User.username)
        .join(Session, Session.id == Message.session_id).join(User, User.id == Session.user_id)
        .where(*where).order_by(Message.created_at.desc()).limit(limit))).all()
    question_ids = [row[0] for row in rows]
    answers: dict[uuid.UUID, tuple] = {}
    if question_ids:
        runs = (await db.execute(select(AgentRun.user_message_id, AgentRun.assistant_message_id, AgentRun.mode)
                                 .where(AgentRun.user_message_id.in_(question_ids)))).all()
        answer_ids = [run[1] for run in runs if run[1]]
        cited = set((await db.scalars(select(MessageCitation.message_id).distinct()
                                      .where(MessageCitation.message_id.in_(answer_ids)))).all()) if answer_ids else set()
        answers = {run[0]: (run[2], run[1] in cited) for run in runs}
    return {
        'total': total or 0,
        'students': students or 0,
        'items': [{
            'message_id': mid, 'session_id': sid, 'student_id': uid, 'student_name': name,
            'question': content if len(content) <= 1000 else content[:1000] + '…',
            'created_at': created, 'session_deleted': deleted,
            'answer_mode': answers.get(mid, (None, False))[0],
            'cited': answers.get(mid, (None, False))[1],
        } for mid, content, created, sid, deleted, uid, name in rows],
    }
