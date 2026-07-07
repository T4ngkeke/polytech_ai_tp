"""
trace_service.py — [v8.0] per-message agent trace telemetry.

One `TraceBuilder` instance threads through a single chat request, accumulating
per-node latencies, degradation flags, retrieval/threshold counts, the self-eval
verdict, and a log-only injection red-flag. At the end it `flush()`es exactly one
`AgentTraceLog` row (async, off the hot path). Write-only — nothing in the live
path reads it back; it feeds the health panel and the self-eval verdict analysis.

The red-flag is a *log-only* audit sort key: it never blocks or filters a message
(the security boundary is SQL tenant isolation, not this regex).
"""

import re
import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import AgentTraceLog

# Log-only injection-attempt heuristics. Deliberately loose and cheap — a false
# positive only nudges a message up the teacher's audit sort, never rejects it.
_RED_FLAG_RE = re.compile(
    r"ignore\s+(?:all\s+|the\s+)?(?:previous|above|prior)\s+instructions"
    r"|disregard\s+(?:all\s+|the\s+)?(?:previous|above|prior)"
    r"|forget\s+(?:everything|all|the)\b"
    r"|you\s+are\s+now\b"
    r"|system\s+prompt"
    r"|reveal\s+(?:the\s+)?(?:answer|solution|prompt|system)",
    re.IGNORECASE,
)


class TraceBuilder:
    """Accumulates one message's trace, then flushes a single AgentTraceLog row."""

    def __init__(
        self,
        *,
        session_id: uuid.UUID | None = None,
        lab_id: uuid.UUID | None = None,
    ):
        self.session_id = session_id
        self.lab_id = lab_id
        self._starts: dict[str, float] = {}
        self.node_latencies: dict[str, int] = {}
        self.degraded_flags: dict[str, bool] = {}
        self.fields: dict[str, object] = {}
        self.red_flag = False

    def start(self, node: str) -> None:
        self._starts[node] = time.monotonic()

    def stop(self, node: str) -> None:
        start = self._starts.pop(node, None)
        if start is not None:
            self.node_latencies[node] = int((time.monotonic() - start) * 1000)

    def flag(self, key: str) -> None:
        """Record that a fallback fired (embedding / rerank / router / …)."""
        self.degraded_flags[key] = True

    def set(self, key: str, value: object) -> None:
        """Set a scalar trace field (route / recall_count / rerank_filtered_count /
        all_filtered / verdict / retry_flipped)."""
        self.fields[key] = value

    def mark_if_suspicious(self, message: str) -> None:
        """Log-only: raise the injection red-flag if the message looks like a
        prompt-injection attempt. Never blocks — audit sort key only."""
        if message and _RED_FLAG_RE.search(message):
            self.red_flag = True

    async def flush(self, db: AsyncSession, message_id: uuid.UUID | None = None) -> None:
        """Persist exactly one AgentTraceLog row for this message."""
        db.add(AgentTraceLog(
            message_id=message_id,
            session_id=self.session_id,
            lab_id=self.lab_id,
            route=self.fields.get("route", "unknown"),
            node_latencies=self.node_latencies or None,
            degraded_flags=self.degraded_flags or None,
            recall_count=self.fields.get("recall_count"),
            rerank_filtered_count=self.fields.get("rerank_filtered_count"),
            all_filtered=bool(self.fields.get("all_filtered", False)),
            verdict=self.fields.get("verdict"),
            retry_flipped=self.fields.get("retry_flipped"),
            red_flag=self.red_flag,
        ))
        await db.flush()
