"""
services/failure_log.py — failures the user does not see, for monitoring.

Some services may fail without stopping the answer: the reranker (the merged
order is kept), the embeddings (full-text search only), a picture view, the
TP text, a session title. The answer still comes, so nobody notices. Each such
failure is recorded here and shown in Grafana (Answers dashboard, "Service
failures"). Failed model calls are not recorded here: model_call_usage keeps
their status.

record() only adds to a list in memory, so it never slows or breaks the
request. The backend (main.py lifespan) and the worker (worker/main.py) write
the list to the service_failures table every few seconds. Without a writer
(tests, scripts) the list keeps the latest entries only.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

FLUSH_SECONDS = 10.0
_pending: deque[tuple[str, str, datetime]] = deque(maxlen=1000)


def record(component: str, kind: str) -> None:
    """One failure: component (rerank, embedding, vision...) and the error name."""
    _pending.append((component[:32], (kind or 'unknown')[:64], datetime.now(timezone.utc)))


async def flush(session_factory) -> int:
    """Write the recorded failures; return how many. A failed write keeps them
    for the next try (as far as the list's length allows)."""
    if not _pending:
        return 0
    from backend.app.models import ServiceFailure

    rows = []
    while _pending:
        rows.append(_pending.popleft())
    try:
        async with session_factory() as db:
            db.add_all([ServiceFailure(component=c, kind=k, created_at=t) for c, k, t in rows])
            await db.commit()
    except Exception as exc:  # noqa: BLE001 - monitoring must never break the app
        logger.warning('Service failures not written (%s)', type(exc).__name__)
        _pending.extendleft(reversed(rows))
        return 0
    return len(rows)


async def run_writer(session_factory, every: float = FLUSH_SECONDS) -> None:
    """Write the failures every few seconds until cancelled, then once more."""
    try:
        while True:
            await asyncio.sleep(every)
            await flush(session_factory)
    except asyncio.CancelledError:
        await flush(session_factory)
        raise
