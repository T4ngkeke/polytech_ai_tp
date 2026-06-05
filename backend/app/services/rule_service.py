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


async def get_active_rules_for_session(
    db: AsyncSession,
    class_id: uuid.UUID,
    lab_id: uuid.UUID,
    student_id: uuid.UUID,
) -> list[Rule]:
    """
    Fetch all active rules for a chat session in order:
    Class-level → Lab-level → Student-level.
    Used by chat.py to build the system prompt.
    """
    result = await db.execute(
        select(Rule).where(
            Rule.is_active.is_(True),
            Rule.target_id.in_([class_id, lab_id, student_id]),
        )
    )
    all_rules = result.scalars().all()

    # Enforce correct injection order
    level_order = {RuleLevel.class_: 0, RuleLevel.lab: 1, RuleLevel.student: 2}
    target_map = {class_id: RuleLevel.class_, lab_id: RuleLevel.lab, student_id: RuleLevel.student}

    def rule_sort_key(r: Rule) -> int:
        # Match rule to its level by target_id
        matched_level = target_map.get(r.target_id, RuleLevel.student)
        return level_order.get(matched_level, 99)

    return sorted(all_rules, key=rule_sort_key)
