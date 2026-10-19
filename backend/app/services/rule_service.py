"""
services/rule_service.py — Core business logic for 3-tier Rule management.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Rule, RuleLevel


async def upsert_rule(
    db: AsyncSession,
    level: RuleLevel,
    target_id: uuid.UUID,
    rules_text: str,
    is_active: bool = True,
) -> Rule:
    """
    Create or update a rule for a given (level, target_id) pair.
    If a rule already exists for that target, update it in-place.
    """
    result = await db.execute(
        select(Rule).where(Rule.level == level, Rule.target_id == target_id)
    )
    rule = result.scalar_one_or_none()

    if rule is not None:
        rule.rules_text = rules_text
        rule.is_active = is_active
    else:
        rule = Rule(
            level=level,
            target_id=target_id,
            rules_text=rules_text,
            is_active=is_active,
        )
        db.add(rule)

    await db.flush()
    await db.refresh(rule)
    return rule


async def list_rules(
    db: AsyncSession,
    level: str | None = None,
    target_id: uuid.UUID | None = None,
) -> list[Rule]:
    """List rules with optional level and target_id filters."""
    query = select(Rule)
    if level is not None:
        query = query.where(Rule.level == level)
    if target_id is not None:
        query = query.where(Rule.target_id == target_id)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_active_rule_texts(
    db: AsyncSession,
    class_id: uuid.UUID | None,
    lab_id: uuid.UUID | None,
    user_id: uuid.UUID,
) -> dict[str, str | None]:
    """Return the active Class/Lab/Student rule texts (or None) for prompt assembly.

    Used by the chat agent's synthesize node, which injects the three tiers in
    Class → Lab → Student order. A tier whose target id is None (e.g. a session
    with no lab) is skipped.
    """
    out: dict[str, str | None] = {"class_rules": None, "lab_rules": None, "student_rules": None}
    targets = [
        ("class_rules", RuleLevel.class_, class_id),
        ("lab_rules", RuleLevel.lab, lab_id),
        ("student_rules", RuleLevel.student, user_id),
    ]
    for key, level, target_id in targets:
        if target_id is None:
            continue
        result = await db.execute(
            select(Rule).where(
                Rule.level == level,
                Rule.target_id == target_id,
                Rule.is_active.is_(True),
            )
        )
        rule = result.scalar_one_or_none()
        if rule:
            out[key] = rule.rules_text
    return out
