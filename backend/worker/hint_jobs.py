"""
hint_jobs.py — [v8.0 §10] DB-aware hint-generation job runner (worker side).

`run_hint_job` fulfils a `hint_generate` IngestionJob. The endpoint decides which
exercises to target (none → generating, idempotently) and puts their ids in the
job payload; this runner is deliberately dumb: it pairs answers to exercises (at
generation time, by number), then for each target generates tiered hints via the
injected `generate_fn` and writes hints + hint_status + hint_source.

Worker-only: imports `Answer` to read answer text for generation — never on the
student path (guarded by test_answers_isolation). The generation model calls live
inside `generate_fn`, so this is unit-testable with a fake and no live model.
"""

import uuid
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Answer, Exercise, HintStatus, IngestionJob
from backend.app.services.answer_service import pair_answers
from backend.worker.hints import HintResult

# Given (statement, answer_text), produce a HintResult. Wraps the hint model
# calls (classify/generate/judge) — injected so the runner needs no live model.
GenerateFn = Callable[[str, str], Awaitable[HintResult]]


async def run_hint_job(
    db: AsyncSession, job: IngestionJob, *, generate_fn: GenerateFn,
) -> None:
    """Generate hints for the exercises named in the job payload. Pairs answers
    first (at generation time), then fills each targeted exercise; an exercise
    with no paired answer fails (blind-solve from scratch is deferred)."""
    payload = job.payload or {}
    ex_ids = [uuid.UUID(x) for x in (payload.get("exercise_ids") or [])]
    if not ex_ids:
        return

    exercises = (await db.execute(
        select(Exercise).where(Exercise.id.in_(ex_ids))
    )).scalars().all()

    # Pair answers to exercises by number (generation-time pairing) for every lab
    # touched, so each exercise can find its answer below.
    for lab_id in {e.lab_id for e in exercises}:
        await pair_answers(db, lab_id)

    answers = (await db.execute(
        select(Answer).where(Answer.exercise_id.in_(ex_ids))
    )).scalars().all()
    answer_by_exercise = {a.exercise_id: a for a in answers}

    for exercise in exercises:
        answer = answer_by_exercise.get(exercise.id)
        if answer is None:
            exercise.hint_status = HintStatus.failed
            continue
        result = await generate_fn(exercise.statement, answer.answer_text)
        exercise.hints = result.hints
        exercise.hint_status = result.status
        exercise.hint_source = result.hint_source

    await db.flush()
