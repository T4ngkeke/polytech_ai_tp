"""
learner_service.py — [v7] prompt-literacy learner profile updates.

Maintains a smoothed effort window (EMA) per (student, lab) and derives a
coaching level with hysteresis, so a single lazy message doesn't flip the tone.
Never gates; stores structured values only. Teacher override is respected.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import CoachingLevel, LearnerProfile

# Smoothing factor for the sliding window (higher = more reactive).
EMA_ALPHA = 0.4
# Ignore the first few samples (cold start) → stay neutral.
WARMUP_SAMPLES = 3
# Hysteresis watermarks on the smoothed score.
LOW_WATERMARK = 0.35
HIGH_WATERMARK = 0.60


def _next_level(current: CoachingLevel, ema: float, samples: int) -> CoachingLevel:
    if samples < WARMUP_SAMPLES:
        return CoachingLevel.neutral
    if ema <= LOW_WATERMARK:
        return CoachingLevel.low
    if ema >= HIGH_WATERMARK:
        return CoachingLevel.high
    # In the hysteresis band: keep the current level (no flapping).
    return current


async def record_effort(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    lab_id: uuid.UUID,
    score: float,
) -> LearnerProfile:
    """Fold one effort score into the (student, lab) profile and return it."""
    profile = (
        await db.execute(
            select(LearnerProfile).where(
                LearnerProfile.user_id == user_id,
                LearnerProfile.lab_id == lab_id,
            )
        )
    ).scalar_one_or_none()

    if profile is None:
        profile = LearnerProfile(
            id=uuid.uuid4(), user_id=user_id, lab_id=lab_id,
            effort_ema=score, samples=1,
        )
        db.add(profile)
    else:
        profile.effort_ema = EMA_ALPHA * score + (1 - EMA_ALPHA) * profile.effort_ema
        profile.samples += 1

    # Teacher override pins the level; otherwise derive it with hysteresis.
    if not profile.teacher_override:
        profile.coaching_level = _next_level(
            profile.coaching_level, profile.effort_ema, profile.samples
        )

    await db.commit()
    await db.refresh(profile)
    return profile
