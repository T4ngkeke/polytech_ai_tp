"""One persisted research run: commit the answer before returning it.

The chat endpoint calls run_persisted_research. Every model call is billed on
the student's quota as it happens; embedding and rerank calls are counted.
Duplicate request IDs return the existing run, never a second model
invocation or charge. A cancelled run stops before its next tool call.
"""
from __future__ import annotations

import asyncio
import time

from sqlalchemy import select

from backend.app.agent.course_tools import CourseTools
from backend.app.agent.history import estimate_tokens
from backend.app.agent.result import ResearchRunStopped
from backend.app.agent.runner import ResearchRunner
from backend.app.agent.tools import PerCallTools, ResearchScope
from backend.app.database import AsyncSessionLocal
from backend.app.models import AgentRun
from backend.app.services.research_runs import ResearchRuns
from backend.app.services.research_usage import ResearchUsage


class _RunningTools(PerCallTools):
    """PerCallTools that refuse every call once the run is no longer running."""

    async def execute(self, name, arguments):
        async with self.session_factory() as db:
            state = await db.scalar(select(AgentRun.status).where(AgentRun.id == self.scope.run_id))
        if state != 'running':
            raise ResearchRunStopped('run_not_running')
        return await super().execute(name, arguments)


async def run_persisted_research(*, scope: ResearchScope, question: str,
                                 client_request_id: str, model, model_name: str,
                                 token_alpha: float, token_beta: float,
                                 history: list[dict] | None = None,
                                 teaching_rules: str = '', research_note: str = '', embed_fn=None,
                                 rerank_fn=None, embed_model: str = '',
                                 rerank_model: str = '', session_factory=AsyncSessionLocal,
                                 tool_factory=None, on_phase=None, vision_fn=None,
                                 vision_model: str = '') -> dict:
    """model: agent.model.ResearchModel (or a test double with the same call)."""
    runs = ResearchRuns(session_factory)
    usage = ResearchUsage(session_factory)
    started = await runs.begin_run(scope=scope, question=question,
                                   client_request_id=client_request_id)
    if not started.created:
        return await runs.load_result(run_id=started.run_id, user_id=scope.user_id)
    def counted_non_token(fn, purpose, model):
        if fn is None:
            return None
        async def counted(*args):
            tick = time.monotonic()
            status = 'ok'
            try:
                return await fn(*args)
            except BaseException:
                status = 'failed'
                raise
            finally:
                await usage.record_non_token_call(run_id=started.run_id,
                    purpose=purpose, model=model or purpose,
                    elapsed_seconds=time.monotonic() - tick, status=status)
        return counted

    async def counted_vision(image, mime, question, width, height):
        # Image tokenization varies by provider. Reserve a bounded conservative
        # image estimate; use reported usage for final settlement where available.
        estimate = min(3000, 512 + 384 * ((width + 511) // 512) *
                       ((height + 511) // 512)) + estimate_tokens(question)
        call_id = await usage.reserve(run_id=started.run_id, purpose='vision',
            model=vision_model, input_tokens=estimate, max_output_tokens=700,
            alpha=token_alpha, beta=token_beta, lifetime_seconds=60)
        tick = time.monotonic()
        try:
            reply = await vision_fn(image, mime, question)
        except BaseException as exc:
            await asyncio.shield(usage.settle(call_id=call_id,
                input_tokens=estimate, output_tokens=0,
                status='cancelled' if isinstance(exc, asyncio.CancelledError) else 'failed',
                elapsed_seconds=time.monotonic() - tick, usage_source='estimated'))
            raise
        await usage.settle(call_id=call_id,
            input_tokens=reply.input_tokens if reply.input_tokens is not None else estimate,
            output_tokens=reply.output_tokens if reply.output_tokens is not None
            else estimate_tokens(reply.observation),
            status='ok', elapsed_seconds=time.monotonic() - tick,
            usage_source='actual' if reply.input_tokens is not None
            and reply.output_tokens is not None else 'estimated')
        return reply

    tools = (tool_factory(scope) if tool_factory else
             _RunningTools(scope, session_factory,
                embed_fn=counted_non_token(embed_fn, 'embedding', embed_model),
                rerank_fn=counted_non_token(rerank_fn, 'rerank', rerank_model),
                vision_fn=counted_vision if vision_fn else None))
    runner = ResearchRunner(CourseTools(tools),
        usage.wrap_model(run_id=started.run_id, model=model_name,
                         alpha=token_alpha, beta=token_beta, call=model),
        on_phase=on_phase)
    try:
        result = await runner.run(question, history=history,
                                  teaching_rules=teaching_rules, research_note=research_note)
        await runs.finish_run(started.run_id, result)
    except asyncio.CancelledError:
        await asyncio.shield(runs.request_cancel(started.run_id, scope.user_id))
        await asyncio.shield(runs.mark_terminal(started.run_id, status='cancelled',
                                               stop_reason='client_cancelled'))
        raise
    except ResearchRunStopped:
        await runs.mark_terminal(started.run_id, status='cancelled',
                                 stop_reason='client_cancelled')
        raise
    except Exception:
        state = await runs.get_status(started.run_id)
        await runs.mark_terminal(started.run_id,
            status='cancelled' if state == 'cancelling' else 'failed',
            stop_reason='client_cancelled' if state == 'cancelling'
            else 'research_or_save_failed')
        raise
    return await runs.load_result(run_id=started.run_id, user_id=scope.user_id)
