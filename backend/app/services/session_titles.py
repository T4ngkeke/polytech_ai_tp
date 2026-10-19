"""
services/session_titles.py — a conversation's title, from its first exchange.

A new conversation has no title. When its first answer is saved, the answer
model writes a short title from the question and the answer, in the student's
language. If the model fails, the first line of the question is the title. A
title the student already wrote is kept. Teacher test sessions get no title.
The call is not charged to the student's quota (a few tokens).
"""

import logging
import re
import uuid

from sqlalchemy import func, select

from backend.app.models import Message, SenderType, Session

logger = logging.getLogger(__name__)

TITLE_PROMPT = (
    'Write a short title, at most 6 words, for this conversation between a student and a '
    'course assistant. Use the language of the student. Name the topic, not the student. '
    'Reply with the title only: no quotes, no final period.')
MAX_CHARS = 60
QUESTION_CHARS = 1000
ANSWER_CHARS = 1500


def clean_title(text: str | None) -> str | None:
    """The first non-empty line, without quotes, markdown or a final period."""
    for line in (text or '').splitlines():
        line = re.sub(r'^(title|titre)\s*:\s*', '', line.strip(), flags=re.I)
        line = line.strip(' \t#*_`"\'«»“”').rstrip('.').strip()
        if line:
            return line if len(line) <= MAX_CHARS else line[:MAX_CHARS - 1].rstrip() + '…'
    return None


async def _untitled_first_exchange(factory, session_id: uuid.UUID) -> bool:
    async with factory() as db:
        session = await db.get(Session, session_id)
        if session is None or session.is_test or (session.title or '').strip():
            return False
        answers = await db.scalar(select(func.count(Message.id)).where(
            Message.session_id == session_id, Message.sender == SenderType.llm))
        return answers == 1


async def title_first_exchange(factory, session_id: uuid.UUID, model, question: str,
                               answer: str, *, timeout: float = 20) -> str | None:
    """Give an untitled conversation a title after its first answer; return it,
    or None when the conversation keeps its title."""
    if not await _untitled_first_exchange(factory, session_id):
        return None
    title = None
    try:
        reply = await model([
            {'role': 'system', 'content': TITLE_PROMPT},
            {'role': 'user', 'content': f'Student: {question[:QUESTION_CHARS]}\n\n'
                                        f'Assistant: {answer[:ANSWER_CHARS]}'},
        ], 24, timeout, purpose='title')
        title = clean_title(reply.content)
    except Exception as exc:          # the conversation still gets a title
        logger.warning('Session title failed (%s); using the question.', type(exc).__name__)
    title = title or clean_title(question) or None
    if title is None:
        return None
    async with factory() as db:
        session = await db.get(Session, session_id)
        # The student may have renamed it while the model was writing.
        if session is None or (session.title or '').strip():
            return None
        session.title = title
        await db.commit()
    return title
