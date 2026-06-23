"""
router_service.py — [v7.1] low-confidence routing telemetry.

The embedding-kNN router falls back to `rag` when no intent clears the confidence
threshold. Those queries are the most valuable training data for a future BERT/XLM-R
router, so we persist them. Write-only — nothing in the live path reads this back.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import RouterQueryLog


async def log_low_confidence_query(
    db: AsyncSession,
    *,
    message: str,
    route: str,
    top_similarity: float,
    lab_id: uuid.UUID | None = None,
) -> None:
    """Persist a below-threshold routing decision for later analysis."""
    db.add(RouterQueryLog(
        id=uuid.uuid4(),
        message=message,
        chosen_route=route,
        top_similarity=top_similarity,
        lab_id=lab_id,
    ))
    await db.flush()
