"""
services/rule_service.py — the teacher's rules for the tutor, as lists.

A class, a lab and a student (in one class) each have a list of rules
(`rule_items`). Each rule can be switched off, edited or deleted. The active
rules of each level go into the prompt as a numbered list, class rules first,
then the lab's, then the student's (routers/chat.py).

BUILT_IN lists ready-made rules a teacher adds with one click. A rule keeps
its `source` (a ready-made rule or one of the teacher's saved rules), so the
teacher page shows which buttons are in the list and a second click removes it.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import RuleItem

MAX_ITEMS = 30          # per class, lab or student
MAX_TEXT = 1000         # characters per rule

# Ready-made rules (English: the model follows them whatever the student's language).
BUILT_IN = [
    {"key": "guide", "name": "Guide, don't solve",
     "text": "Do not give the full solution. Guide the student with questions and hints so that they find the answer themselves."},
    {"key": "short", "name": "Short answers",
     "text": "Keep answers short: a few sentences, unless the student asks for more detail."},
    {"key": "hints", "name": "Hints, not full code",
     "text": "Do not write complete programs. Give hints, pseudo-code or small fragments only."},
    {"key": "ask-tried", "name": "Ask what they tried",
     "text": "Before helping with an exercise, ask the student what they have already tried."},
    {"key": "steps", "name": "Step by step",
     "text": "Explain step by step, with one small example."},
    {"key": "check", "name": "Check understanding",
     "text": "End an explanation with one short question that checks the student understood."},
    {"key": "point-course", "name": "Point to the course",
     "text": "When the course covers the topic, say which file and page the student should read."},
    {"key": "vocabulary", "name": "Course vocabulary",
     "text": "Use the notation and the vocabulary of the course documents."},
    {"key": "stay-course", "name": "Stay on the course",
     "text": "Only answer questions about this course. Politely decline other topics."},
    {"key": "french", "name": "Answer in French",
     "text": "Always answer in French."},
]
BUILT_IN_KEYS = {p["key"] for p in BUILT_IN}


async def list_items(db: AsyncSession, *, level: str, target_id: uuid.UUID,
                     class_id: uuid.UUID | None = None) -> list[RuleItem]:
    """The rules of one class, lab or student, in their order.
    For a student, class_id names the class the rules apply in."""
    query = select(RuleItem).where(RuleItem.level == level, RuleItem.target_id == target_id)
    if level == "student":
        query = query.where(RuleItem.class_id == class_id)
    return list((await db.scalars(query.order_by(RuleItem.position, RuleItem.created_at))).all())


async def add_item(db: AsyncSession, *, level: str, target_id: uuid.UUID, class_id: uuid.UUID,
                   text: str, is_active: bool = True, source: str | None = None,
                   position: int | None = None) -> RuleItem:
    """Add a rule at the end of the list, or at `position` (an undone delete
    goes back to its place). ValueError('too_many_rules') past MAX_ITEMS."""
    existing = await list_items(db, level=level, target_id=target_id, class_id=class_id)
    if len(existing) >= MAX_ITEMS:
        raise ValueError("too_many_rules")
    if position is None or position > len(existing):
        position = len(existing)
    # Renumber the list so the new rule sits at `position`.
    for index, other in enumerate(existing):
        other.position = index if index < position else index + 1
    item = RuleItem(level=level, target_id=target_id, class_id=class_id, text=text.strip(),
                    is_active=is_active, source=source, position=position)
    db.add(item)
    await db.flush()
    return item


_UNSET = object()


async def update_item(db: AsyncSession, item: RuleItem, *, text: str | None = None,
                      is_active: bool | None = None, source=_UNSET) -> RuleItem:
    if text is not None:
        item.text = text.strip()
    if is_active is not None:
        item.is_active = is_active
    if source is not _UNSET:
        item.source = source
    item.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return item


def numbered(texts: list[str]) -> str | None:
    """The rules of one level for the prompt: '1. …' per line, or None."""
    return "\n".join(f"{n}. {t}" for n, t in enumerate(texts, 1)) if texts else None


async def get_active_rule_texts(
    db: AsyncSession,
    class_id: uuid.UUID | None,
    lab_id: uuid.UUID | None,
    user_id: uuid.UUID,
) -> dict[str, str | None]:
    """The active class, lab and student rules as numbered lists (or None).

    A level whose target is unknown (a session without a lab) is skipped. The
    student's rules are the ones of this class only.
    """
    out: dict[str, str | None] = {"class_rules": None, "lab_rules": None, "student_rules": None}
    targets = [("class_rules", "class", class_id), ("lab_rules", "lab", lab_id),
               ("student_rules", "student", user_id)]
    for key, level, target_id in targets:
        if target_id is None or (level == "student" and class_id is None):
            continue
        items = await list_items(db, level=level, target_id=target_id, class_id=class_id)
        out[key] = numbered([i.text for i in items if i.is_active])
    return out
