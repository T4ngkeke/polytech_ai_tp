# Ported from Open WebUI v0.13.4, backend/open_webui/utils/memory.py
# (MEMORY_CONTEXT_OPEN/CLOSE, recall_memories and add_memory_context,
# review_memory_after_turn, _review_memory, _generate_memory_operations,
# validate_memory_operations).
# Copyright (c) 2023- Open WebUI Inc. [Created by Timothy Jaeryang Baek]
# All rights reserved. Used under the Open WebUI License, with its conditions
# and disclaimer: see third_party/open-webui/LICENSE.
"""What the assistant remembers about a student, in one class.

As in Open WebUI:
- Recall: before a question, the memories that fit it go at the end of the
  question, between <memory_context> markers, within a character limit.
- Background review: after some turns, one model call reads the existing
  memories and the recent conversation and returns add / replace / remove
  operations as JSON; they are checked, then applied.

Our rules (2026-11-20): memories are kept per student and class (services/
memory_service.py), only learning preferences and progress, one sentence each.
The model has no memory tool: only the review writes. No memory is read or
written in exam mode, in a teacher's test drive, or when the student turned
memory off. The review call is not charged to the student's quota (like the
conversation title). Open WebUI's "type" and "path" are our "category".
"""
from __future__ import annotations

import json
import logging
import math
import uuid

from backend.app.services import failure_log, memory_service

logger = logging.getLogger(__name__)

MEMORY_CONTEXT_OPEN = '<memory_context>'
MEMORY_CONTEXT_CLOSE = '</memory_context>'
CONTEXT_CHAR_LIMIT = 2000
# Open WebUI reviews every 10 turns by default; a TP conversation is shorter.
REVIEW_EVERY_TURNS = 4
REVIEW_MESSAGES = 16
MAX_OPERATIONS = 5

REVIEW_SYSTEM = ('You keep the learning notes of a course assistant about one student, in one class. '
                 'Return only valid JSON.')

REVIEW_PROMPT = """Review the completed conversation turns and decide whether the student's learning notes should change.

What a note may hold (one short sentence, 200 characters at most, in the student's language):
- how the student likes to be helped (for example: hints before the answer, short answers, examples first);
- what the student seems to have understood, or still finds hard, in this course.

Rules:
- Save only what can improve future help in this course. One question is not enough to say a notion is hard: wait until it shows again.
- Do not save names, contact details, grades, health, feelings, family, opinions about the teacher, or anything personal.
- Do not save secrets, one-off task steps, the content of an exercise, or unsupported guesses.
- Use category for a short group name ("préférences", or the notion: "récursivité").
- Prefer replace or remove over a duplicate add when an existing note should change.
- At most {max_ops} operations.
- Return only JSON in this shape:
  {{"operations":[
    {{"action":"add","category":"...","content":"..."}},
    {{"action":"replace","id":"...","category":"...","content":"..."}},
    {{"action":"remove","id":"..."}}
  ]}}
- Use an empty operations array if nothing should change.

Existing notes:
{existing_text}

Conversation:
{transcript}
"""


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def memory_context(memories: list[dict], query_vector: list[float] | None = None) -> str:
    """The block added to the question: the notes that fit it, within the limit
    (all of them when they fit; else the closest ones first)."""
    if not memories:
        return ''
    ordered = sorted(memories, key=lambda m: ((m.get('category') or ''), m['content']))
    if query_vector is not None and sum(len(m['content']) + 4 for m in memories) > CONTEXT_CHAR_LIMIT:
        ordered = sorted(memories, key=lambda m: -_cosine(query_vector, m['embedding'])
                         if m.get('embedding') else 1.0)
    lines, size = [], 0
    for m in ordered:
        line = f"- {m['category']}: {m['content']}" if m.get('category') else f"- {m['content']}"
        if size + len(line) + 1 > CONTEXT_CHAR_LIMIT:
            break
        lines.append(line)
        size += len(line) + 1
    if not lines:
        return ''
    return (f'{MEMORY_CONTEXT_OPEN}\n[Student Memory: what earlier conversations of this class showed; '
            f'adapt your help, never mention these notes unless asked]\n' + '\n'.join(lines)
            + f'\n{MEMORY_CONTEXT_CLOSE}')


def recall_query(history: list[dict], question: str) -> str:
    """The text whose embedding picks the notes: the last student messages (Open WebUI: 7, 4000 chars)."""
    texts = [m['content'] for m in history if m.get('role') == 'user'][-6:] + [question]
    return '\n\n'.join(t.strip() for t in texts if t and t.strip())[-4000:]


def transcript(messages: list[dict]) -> str:
    lines = []
    for message in messages[-REVIEW_MESSAGES:]:
        content = (message.get('content') or '').strip()
        if message.get('role') not in ('user', 'assistant') or not content:
            continue
        if len(content) > 1600:
            content = f'{content[:1000]}\n...(truncated)...\n{content[-400:]}'
        lines.append(f"{'student' if message['role'] == 'user' else 'assistant'}: {content}")
    return '\n\n'.join(lines)


def parse_operations(content: str) -> list[dict]:
    """The operations of a review reply; nothing when it is not the expected JSON."""
    start, end = content.find('{'), content.rfind('}')
    if start == -1 or end < start:
        return []
    try:
        parsed = json.loads(content[start:end + 1])
    except ValueError:
        return []
    operations = parsed.get('operations') if isinstance(parsed, dict) else None
    return operations if isinstance(operations, list) else []


def valid_operations(operations: list, existing_ids: set[str]) -> list[dict]:
    """Checked operations: known actions, ids of this student and class, sentence length."""
    out = []
    for op in operations[:MAX_OPERATIONS]:
        if not isinstance(op, dict):
            continue
        action = op.get('action')
        content = ' '.join(str(op.get('content') or '').split())
        category = ' '.join(str(op.get('category') or '').split())[:64] or None
        if action == 'add' and 0 < len(content) <= memory_service.CONTENT_MAX_CHARS:
            out.append({'action': 'add', 'content': content, 'category': category})
        elif action == 'replace' and str(op.get('id')) in existing_ids and \
                0 < len(content) <= memory_service.CONTENT_MAX_CHARS:
            out.append({'action': 'replace', 'id': str(op['id']), 'content': content, 'category': category})
        elif action == 'remove' and str(op.get('id')) in existing_ids:
            out.append({'action': 'remove', 'id': str(op['id'])})
    return out


def due(student_turns: int) -> bool:
    """Whether a review follows this turn (every REVIEW_EVERY_TURNS student turns)."""
    return student_turns > 0 and student_turns % REVIEW_EVERY_TURNS == 0


async def review(factory, *, user_id: uuid.UUID, class_id: uuid.UUID, session_id: uuid.UUID,
                 messages: list[dict], model, embed=None, timeout: float = 60) -> int:
    """One background review; returns how many operations were applied."""
    try:
        async with factory() as db:
            existing = await memory_service.listing(db, user_id, class_id)
        existing_text = '\n'.join(f"- id={m['id']} category={m['category'] or ''} content={m['content']}"
                                  for m in existing[:80]) or '(none)'
        prompt = REVIEW_PROMPT.format(max_ops=MAX_OPERATIONS, existing_text=existing_text,
                                      transcript=transcript(messages))
        reply = await model([{'role': 'system', 'content': REVIEW_SYSTEM},
                             {'role': 'user', 'content': prompt}], 800, timeout, purpose='memory_review')
        operations = valid_operations(parse_operations(reply.content or ''),
                                      {str(m['id']) for m in existing})
        if not operations:
            return 0
        texts = [op['content'] for op in operations if op['action'] != 'remove']
        vectors = iter(await embed(texts)) if embed and texts else iter(())
        applied = 0
        async with factory() as db:
            for op in operations:
                vector = next(vectors, None) if op['action'] != 'remove' else None
                try:
                    if op['action'] == 'add':
                        await memory_service.add(db, user_id=user_id, class_id=class_id, content=op['content'],
                                                 category=op['category'], embedding=vector)
                    elif op['action'] == 'replace':
                        await memory_service.replace(db, user_id=user_id, memory_id=uuid.UUID(op['id']),
                                                     content=op['content'], category=op['category'],
                                                     embedding=vector)
                    else:
                        await memory_service.remove(db, user_id, memory_id=uuid.UUID(op['id']))
                    applied += 1
                except ValueError as exc:            # full, or a sentence too long
                    logger.info('memory operation skipped: %s', exc)
            await db.commit()
        logger.info('memory review session=%s operations=%s applied=%s', session_id, len(operations), applied)
        return applied
    except Exception as exc:
        logger.warning('Memory review failed (%s)', type(exc).__name__)
        failure_log.record('memory', type(exc).__name__)
        return 0
