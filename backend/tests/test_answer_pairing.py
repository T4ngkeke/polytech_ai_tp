"""
test_answer_pairing.py — [v8.0 §9] pair uploaded Answers to Exercises by number.

Pairing links `Answer.exercise_id` at generation time, by `number_normalized`
within the lab. A standalone corrigé may pin its target document (multi-file labs
never cross-match); in-lab number collisions are reported, not guessed.
"""

import uuid

import pytest

from backend.app.models import (
    Answer, Class, Document, Exercise, Lab, UserRole,
)
from backend.app.services.answer_service import PairingReport, pair_answers
from backend.tests.conftest import make_user


async def _seed_lab(session):
    teacher = make_user(role=UserRole.teacher)
    session.add(teacher)
    await session.flush()
    cls = Class(id=uuid.uuid4(), name="Algo", teacher_id=teacher.id,
                invite_code=uuid.uuid4().hex[:6])
    session.add(cls)
    await session.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name="Lab")
    session.add(lab)
    await session.flush()
    return teacher, cls, lab


def _doc(cls, lab, teacher, **kw):
    return Document(id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="d.pdf",
                    storage_path="/x", content_hash=uuid.uuid4().hex,
                    uploaded_by=teacher.id, **kw)


@pytest.mark.asyncio
async def test_pairs_answer_to_exercise_by_number(db_session):
    teacher, cls, lab = await _seed_lab(db_session)
    td = _doc(cls, lab, teacher)
    db_session.add(td)
    await db_session.flush()
    ex = Exercise(id=uuid.uuid4(), document_id=td.id, class_id=cls.id, lab_id=lab.id,
                  number="3", number_normalized=3, statement="s")
    ans = Answer(id=uuid.uuid4(), document_id=td.id, number_raw="3",
                 number_normalized=3, answer_text="42")
    db_session.add_all([ex, ans])
    await db_session.commit()

    report = await pair_answers(db_session, lab.id)

    assert isinstance(report, PairingReport)
    assert report.paired == 1
    await db_session.refresh(ans)
    assert ans.exercise_id == ex.id


@pytest.mark.asyncio
async def test_number_collision_is_ambiguous_not_paired(db_session):
    teacher, cls, lab = await _seed_lab(db_session)
    td_a = _doc(cls, lab, teacher)
    td_b = _doc(cls, lab, teacher)
    db_session.add_all([td_a, td_b])
    await db_session.flush()
    # two exercises with the same normalized number in the lab
    db_session.add_all([
        Exercise(id=uuid.uuid4(), document_id=td_a.id, class_id=cls.id, lab_id=lab.id,
                 number="3", number_normalized=3, statement="a"),
        Exercise(id=uuid.uuid4(), document_id=td_b.id, class_id=cls.id, lab_id=lab.id,
                 number="3", number_normalized=3, statement="b"),
    ])
    ans = Answer(id=uuid.uuid4(), document_id=td_a.id, number_raw="3",
                 number_normalized=3, answer_text="42")
    db_session.add(ans)
    await db_session.commit()

    report = await pair_answers(db_session, lab.id)

    assert report.paired == 0
    assert "3" in report.ambiguous
    await db_session.refresh(ans)
    assert ans.exercise_id is None


@pytest.mark.asyncio
async def test_corrige_pins_to_its_target_document(db_session):
    teacher, cls, lab = await _seed_lab(db_session)
    td_a = _doc(cls, lab, teacher)   # the exercise doc we want to pair to
    td_b = _doc(cls, lab, teacher)   # a different doc with the same number (bait)
    db_session.add_all([td_a, td_b])
    await db_session.flush()
    ex_a = Exercise(id=uuid.uuid4(), document_id=td_a.id, class_id=cls.id, lab_id=lab.id,
                    number="3", number_normalized=3, statement="target")
    db_session.add_all([
        ex_a,
        Exercise(id=uuid.uuid4(), document_id=td_b.id, class_id=cls.id, lab_id=lab.id,
                 number="3", number_normalized=3, statement="bait"),
    ])
    # standalone corrigé pinned to td_a
    corrige = _doc(cls, lab, teacher, answers_for_document_id=td_a.id)
    db_session.add(corrige)
    await db_session.flush()
    ans = Answer(id=uuid.uuid4(), document_id=corrige.id, number_raw="3",
                 number_normalized=3, answer_text="42")
    db_session.add(ans)
    await db_session.commit()

    report = await pair_answers(db_session, lab.id)

    assert report.paired == 1          # pinned → unambiguous despite the collision
    await db_session.refresh(ans)
    assert ans.exercise_id == ex_a.id
