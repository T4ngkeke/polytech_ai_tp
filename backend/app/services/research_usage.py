"""M4-C per-call reservation and settlement for the opt-in research path.

Each method opens and closes a short transaction. In particular no row lock is
held while a model performs network I/O. Unknown provider state keeps its
reservation until an explicit reconciliation; expiry alone never means free.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import date, timedelta
from typing import Awaitable, Callable

from sqlalchemy import func, select

from backend.app.agent.history import estimate_tokens
from backend.app.agent.model import ModelReply
from backend.app.agent.result import ResearchQuotaExceeded, ResearchRunStopped
from backend.app.database import AsyncSessionLocal
from backend.app.models import AgentRun, ModelCallUsage, UsageReservation, UsageStat, User, _utcnow
from backend.app.services.billing import compute_billed_tokens


class ResearchCallConflict(ValueError):
    pass


def estimated_input(messages: list[dict], tools: list[dict] | None = None) -> int:
    """Conservative input estimate: message text, tool calls and tool definitions."""
    total = sum(estimate_tokens(m['content'] if isinstance(m.get('content'), str)
                                else json.dumps(m.get('content'), ensure_ascii=False))
                + estimate_tokens(json.dumps(m.get('tool_calls') or '', ensure_ascii=False))
                for m in messages)
    return total + (estimate_tokens(json.dumps(tools, ensure_ascii=False)) if tools else 0)


class ResearchUsage:
    def __init__(self, session_factory=AsyncSessionLocal):
        self.session_factory = session_factory

    async def reserve(self, *, run_id: uuid.UUID, purpose: str, model: str,
                      input_tokens: int, max_output_tokens: int,
                      alpha: float, beta: float, lifetime_seconds: int = 90,
                      call_id: uuid.UUID | None = None) -> uuid.UUID:
        if (input_tokens < 0 or max_output_tokens < 0 or alpha < 0 or beta < 0
                or not purpose or not model):
            raise ValueError('invalid_reservation')
        call_id = call_id or uuid.uuid4()
        today = date.today()
        reserve_tokens = compute_billed_tokens(input_tokens, max_output_tokens,
                                                alpha=alpha, beta=beta)
        async with self.session_factory() as db:
            async with db.begin():
                run = await db.get(AgentRun, run_id)
                if run is None or run.status != 'running':
                    raise ResearchRunStopped('run_not_running')
                # All reservations and settlement for this user take this lock
                # first, serializing the quota check across sessions/runs.
                user = await db.scalar(select(User).where(User.id == run.user_id)
                                       .with_for_update())
                if user is None or user.is_deleted:
                    raise ResearchCallConflict('user_unavailable')
                run = await db.scalar(select(AgentRun).where(AgentRun.id == run_id)
                    .with_for_update().execution_options(populate_existing=True))
                if run.status != 'running':
                    raise ResearchRunStopped('run_not_running')
                existing = await db.get(UsageReservation, call_id)
                if existing is not None:
                    if (existing.run_id == run_id and existing.purpose == purpose
                            and existing.model == model
                            and existing.estimated_input_tokens == input_tokens
                            and existing.max_output_tokens == max_output_tokens
                            and existing.token_alpha == alpha
                            and existing.token_beta == beta):
                        return call_id
                    raise ResearchCallConflict('call_id_payload_conflict')
                consumed = await db.scalar(select(UsageStat.tokens_used).where(
                    UsageStat.user_id == user.id, UsageStat.date == today)) or 0
                held = await db.scalar(select(func.coalesce(func.sum(
                    UsageReservation.reserved_tokens), 0)).where(
                    UsageReservation.user_id == user.id,
                    UsageReservation.date == today,
                    UsageReservation.state.in_(('active', 'unknown')))) or 0
                if consumed + held + reserve_tokens > user.daily_token_quota:
                    raise ResearchQuotaExceeded('daily_token_quota_exceeded')
                db.add(UsageReservation(
                    call_id=call_id, run_id=run_id, user_id=user.id, date=today,
                    reserved_tokens=reserve_tokens, estimated_input_tokens=input_tokens,
                    max_output_tokens=max_output_tokens, token_alpha=alpha,
                    token_beta=beta, purpose=purpose, model=model,
                    state='active', expires_at=_utcnow() + timedelta(seconds=lifetime_seconds),
                ))
        return call_id

    async def settle(self, *, call_id: uuid.UUID, input_tokens: int | None,
                     output_tokens: int | None, status: str, elapsed_seconds: float,
                     usage_source: str) -> ModelCallUsage:
        if usage_source not in ('actual', 'estimated') or elapsed_seconds < 0:
            raise ValueError('invalid_usage')
        async with self.session_factory() as db:
            async with db.begin():
                # Read identity before taking locks, then always lock User first.
                identity = await db.get(UsageReservation, call_id)
                if identity is None:
                    raise ResearchCallConflict('reservation_not_found')
                await db.scalar(select(User).where(User.id == identity.user_id)
                                .with_for_update())
                reservation = await db.scalar(select(UsageReservation)
                    .where(UsageReservation.call_id == call_id).with_for_update()
                    .execution_options(populate_existing=True))
                prior = await db.get(ModelCallUsage, call_id)
                if reservation.state == 'settled':
                    if prior is None:
                        raise ResearchCallConflict('inconsistent_settlement')
                    return prior
                if input_tokens is None:
                    input_tokens = reservation.estimated_input_tokens
                    usage_source = 'estimated'
                if output_tokens is None:
                    output_tokens = 0
                    usage_source = 'estimated'
                if input_tokens < 0 or output_tokens < 0:
                    raise ValueError('invalid_usage')
                billed = compute_billed_tokens(input_tokens, output_tokens,
                    alpha=reservation.token_alpha, beta=reservation.token_beta)
                row = ModelCallUsage(
                    call_id=call_id, run_id=reservation.run_id,
                    purpose=reservation.purpose, model=reservation.model,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    usage_source=usage_source, token_alpha=reservation.token_alpha,
                    token_beta=reservation.token_beta, billed_tokens=billed,
                    elapsed_seconds=elapsed_seconds, status=status,
                )
                db.add(row)
                reservation.state, reservation.settled_tokens = 'settled', billed
                daily = await db.scalar(select(UsageStat).where(
                    UsageStat.user_id == reservation.user_id,
                    UsageStat.date == reservation.date).with_for_update())
                if daily is None:
                    db.add(UsageStat(user_id=reservation.user_id,
                                     date=reservation.date, tokens_used=billed,
                                     request_count=0))
                else:
                    daily.tokens_used += billed
                await db.flush()
                run = await db.scalar(select(AgentRun).where(
                    AgentRun.id == reservation.run_id).with_for_update())
                if run.status in ('failed', 'cancelled'):
                    held = await db.scalar(select(func.count()).select_from(
                        UsageReservation).where(UsageReservation.run_id == run.id,
                        UsageReservation.state.in_(('active', 'unknown')))) or 0
                    if held == 0:
                        run.settlement_status = 'settled'
                return row

    async def record_non_token_call(self, *, run_id: uuid.UUID, purpose: str,
                                    model: str, elapsed_seconds: float,
                                    status: str = 'ok',
                                    call_id: uuid.UUID | None = None) -> uuid.UUID:
        """Embedding/rerank with no token usage: count and latency, zero bill."""
        call_id = call_id or uuid.uuid4()
        async with self.session_factory() as db:
            async with db.begin():
                run = await db.get(AgentRun, run_id)
                if run is None:
                    raise ResearchCallConflict('run_not_found')
                existing = await db.get(ModelCallUsage, call_id)
                if existing is not None:
                    if (existing.run_id == run_id and existing.purpose == purpose
                            and existing.model == model):
                        return call_id
                    raise ResearchCallConflict('call_id_payload_conflict')
                db.add(ModelCallUsage(call_id=call_id, run_id=run_id,
                    purpose=purpose, model=model, input_tokens=None,
                    output_tokens=None, usage_source='none', token_alpha=0,
                    token_beta=0, billed_tokens=0,
                    elapsed_seconds=elapsed_seconds, status=status))
        return call_id

    def wrap_model(self, *, run_id: uuid.UUID, model: str, alpha: float, beta: float,
                   call: Callable[..., Awaitable[ModelReply]]):
        """The research model call (model.ResearchModel), billed call by call.

        Each call reserves its estimated cost on the student's daily quota first
        (ResearchQuotaExceeded stops the run), then settles the reported usage.
        The call's purpose (agent, answer, citation_correction) is recorded.
        """
        async def wrapped(messages: list[dict], max_output: int, timeout: float, *,
                          tools: list[dict] | None = None, purpose: str = 'agent',
                          on_text=None) -> ModelReply:
            estimate = estimated_input(messages, tools)
            call_id = await self.reserve(run_id=run_id, purpose=purpose, model=model,
                input_tokens=estimate, max_output_tokens=max_output,
                alpha=alpha, beta=beta,
                lifetime_seconds=max(30, int(timeout) + 30))
            tick = time.monotonic()
            try:
                reply = await call(messages, max_output, timeout, tools=tools, purpose=purpose,
                                   **({'on_text': on_text} if on_text else {}))
            except BaseException as exc:
                # Provider may have consumed the prompt before returning an
                # error. Record the conservative estimate, not a fictitious 10.
                await asyncio.shield(self.settle(call_id=call_id,
                    input_tokens=estimate, output_tokens=0,
                    status='cancelled' if isinstance(exc, asyncio.CancelledError)
                    else 'failed', elapsed_seconds=time.monotonic() - tick,
                    usage_source='estimated'))
                raise
            output_text = reply.content + json.dumps(reply.tool_calls, ensure_ascii=False)
            await self.settle(call_id=call_id,
                input_tokens=reply.input_tokens if reply.input_tokens is not None else estimate,
                output_tokens=reply.output_tokens if reply.output_tokens is not None
                else estimate_tokens(output_text),
                status='ok', elapsed_seconds=time.monotonic() - tick,
                usage_source='actual' if reply.input_tokens is not None
                and reply.output_tokens is not None else 'estimated')
            return reply
        return wrapped

    async def mark_expired_unknown(self, call_id: uuid.UUID) -> bool:
        """Conservative crash recovery; manual/provider reconciliation releases it.

        Expiry is not proof that a model did not run. Unknown reservations still
        count against quota and cannot be silently charged or released.
        """
        async with self.session_factory() as db:
            async with db.begin():
                identity = await db.get(UsageReservation, call_id)
                if identity is None:
                    return False
                await db.scalar(select(User).where(User.id == identity.user_id)
                                .with_for_update())
                reservation = await db.scalar(select(UsageReservation).where(
                    UsageReservation.call_id == call_id).with_for_update()
                    .execution_options(populate_existing=True))
                if reservation.state != 'active' or reservation.expires_at > _utcnow():
                    return False
                reservation.state = 'unknown'
                return True
