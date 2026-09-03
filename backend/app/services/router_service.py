"""
router_service.py — router-decision telemetry.

The one-call LLM router classifies every message; each decision is persisted to
RouterQueryLog (route + degraded / is_test flags + model / prompt version).
Write-only — nothing in the live path reads it back; it is the labelled data
source for a future distilled router. Called as a BackgroundTask so logging never
blocks the chat response.

The teaching-hotspot ranking used to live here too: it counted the most-asked
exercise numbers per lab, which is not a thing that exists any more.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import RouterQueryLog


async def log_decision(
    db: AsyncSession,
    *,
    message: str,
    route: str,
    degraded: bool,
    latency_ms: int | None,
    model_name: str | None = None,
    prompt_version: str | None = None,
    lab_id: uuid.UUID | None = None,
    is_test: bool = False,
) -> None:
    """Persist one router decision. `degraded` marks timeout/failure fallbacks
    that must be excludable when distilling."""
    db.add(RouterQueryLog(
        id=uuid.uuid4(),
        message=message,
        route=route,
        degraded=degraded,
        is_test=is_test,
        model_name=model_name,
        prompt_version=prompt_version,
        latency_ms=latency_ms,
        lab_id=lab_id,
    ))
    await db.flush()
