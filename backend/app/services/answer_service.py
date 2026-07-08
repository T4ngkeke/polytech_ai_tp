"""
answer_service.py — [v8.0 §9] uploaded-answer pairing (teacher/worker side).

Links `Answer.exercise_id` to the exercise it answers, by `number_normalized`
within the lab. A standalone corrigé may pin its target document
(`Document.answers_for_document_id`) so multi-file labs never cross-match; an
in-lab number collision with no pin is reported (never guessed).

Imports `Answer` — this is teacher/worker code, NOT a student-facing path (the
student path is guarded by test_answers_isolation).
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Answer, Document, Exercise


@dataclass
class PairingReport:
    paired: int = 0
    ambiguous: list[str] = field(default_factory=list)   # number_raw values
    unmatched: list[str] = field(default_factory=list)


async def pair_answers(db: AsyncSession, lab_id: uuid.UUID) -> PairingReport:
    """Pair every still-unpaired Answer in the lab to its Exercise by number.
    Sets `Answer.exercise_id` for unambiguous matches; returns a report of what
    paired, what collided (ambiguous), and what found no exercise (unmatched)."""
    # Exercises in the lab, grouped by normalized number → [(exercise_id, doc_id)].
    ex_rows = (await db.execute(
        select(Exercise.id, Exercise.number_normalized, Exercise.document_id)
        .where(Exercise.lab_id == lab_id)
    )).all()
    by_number: dict[int, list[tuple[uuid.UUID, uuid.UUID]]] = {}
    for r in ex_rows:
        if r.number_normalized is not None:
            by_number.setdefault(r.number_normalized, []).append((r.id, r.document_id))

    # Unpaired answers whose source document is in this lab, with any corrigé pin.
    ans_rows = (await db.execute(
        select(Answer, Document.answers_for_document_id)
        .join(Document, Answer.document_id == Document.id)
        .where(Document.lab_id == lab_id, Answer.exercise_id.is_(None))
    )).all()

    report = PairingReport()
    for ans, pinned_doc_id in ans_rows:
        if ans.number_normalized is None:
            report.unmatched.append(ans.number_raw)
            continue
        candidates = by_number.get(ans.number_normalized, [])
        if pinned_doc_id is not None:
            candidates = [c for c in candidates if c[1] == pinned_doc_id]
        if len(candidates) == 1:
            ans.exercise_id = candidates[0][0]
            report.paired += 1
        elif len(candidates) > 1:
            report.ambiguous.append(ans.number_raw)
        else:
            report.unmatched.append(ans.number_raw)

    await db.flush()
    return report
