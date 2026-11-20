"""feature_service.py — what students may use in a class and in a lab.

Four features: ``upload`` (files and screenshots), ``web`` (web search),
``code`` (the code interpreter), ``deep`` (deep research).

- A class stores one switch per feature (``Class.features``); a missing key is on.
- A lab stores its own choice per feature (``Lab.feature_overrides``): 'on' or
  'off'; a missing key follows the class.
- Exam mode turns every feature off, whatever the switches say.

The chat checks the tools a question asks for against ``effective`` (routers/chat.py).
"""
from __future__ import annotations

from typing import Literal

from sqlalchemy import select

from ..models import Class, Lab

FEATURES = ('upload', 'web', 'code', 'deep')
Feature = Literal['upload', 'web', 'code', 'deep']
Override = Literal['on', 'off', 'inherit']


def class_features(stored: dict | None) -> dict[str, bool]:
    """The class switches, every feature present (a missing key is on)."""
    stored = stored or {}
    return {name: bool(stored.get(name, True)) for name in FEATURES}


def lab_overrides(stored: dict | None) -> dict[str, str]:
    """The lab's own choices, every feature present ('inherit' when not set)."""
    stored = stored or {}
    return {name: stored.get(name) if stored.get(name) in ('on', 'off') else 'inherit'
            for name in FEATURES}


def effective(class_stored: dict | None, lab_stored: dict | None, *, exam_mode: bool) -> dict[str, bool]:
    """What students may use in this lab now."""
    if exam_mode:
        return {name: False for name in FEATURES}
    base = class_features(class_stored)
    own = lab_overrides(lab_stored)
    return {name: base[name] if own[name] == 'inherit' else own[name] == 'on' for name in FEATURES}


def updated_class(stored: dict | None, changes: dict[str, bool]) -> dict[str, bool]:
    """The class switches after a partial update (only the given features change)."""
    return {**class_features(stored), **changes}


def updated_lab(stored: dict | None, changes: dict[str, str]) -> dict[str, str]:
    """The lab choices after a partial update; 'inherit' removes the lab's own choice."""
    out = {name: value for name, value in lab_overrides(stored).items() if value != 'inherit'}
    for name, value in changes.items():
        if value == 'inherit':
            out.pop(name, None)
        else:
            out[name] = value
    return out


async def fill_effective(db, labs: list) -> list:
    """Set ``features`` (what students may use now) on LabResponse objects."""
    ids = {lab.id for lab in labs}
    if not ids:
        return labs
    rows = (await db.execute(
        select(Lab.id, Lab.feature_overrides, Class.features, Class.exam_mode)
        .join(Class, Class.id == Lab.class_id).where(Lab.id.in_(ids)))).all()
    by_lab = {lab_id: effective(cls_features, overrides, exam_mode=bool(exam))
              for lab_id, overrides, cls_features, exam in rows}
    for lab in labs:
        lab.features = by_lab.get(lab.id, {name: False for name in FEATURES})
    return labs
