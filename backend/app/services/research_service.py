"""One persisted research run: commit the answer before returning it.

The chat endpoint calls run_persisted_research for each answer mode: direct
(agent/direct.py), search (agent/search.py) and research (agent/runner.py).
Every model call is billed on
the student's quota as it happens; embedding and rerank calls are counted.
Duplicate request IDs return the existing run, never a second model
invocation or charge. A cancelled run stops before its next tool call.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace

from sqlalchemy import select

from backend.app.agent import direct as direct_mode
from backend.app.agent import search as search_mode
from backend.app.agent.course_tools import CourseTools
from backend.app.agent.direct import DirectRunner
from backend.app.agent.history import DEFAULT_CONTEXT, estimate_tokens
from backend.app.agent.language import ask_language
from backend.app.agent.result import ResearchQuotaExceeded, ResearchRunStopped
from backend.app.agent.runner import ResearchRunner
from backend.app.agent.search import SearchRunner
from backend.app.agent.tools import PerCallTools, ResearchScope
from backend.app.database import AsyncSessionLocal
from backend.app.models import AgentRun
from backend.app.services import failure_log
from backend.app.services.research_runs import STALE_RUN_SECONDS, ResearchRuns
from backend.app.services.research_usage import ResearchUsage


@dataclass(frozen=True)
class ResearchLimits:
    """Limits of one research run; an admin can change them (SystemConfig,
    LIMIT_KEYS). The defaults suit Qwen3.8-27B and Qwen3.8-Flash-Next on one
    DGX Spark (the 2026-10-12 end-to-end test): a long answer can take two
    minutes, so the run keeps total - research seconds for it. 30 tool calls
    (2026-10-15, v4 x 2): runs stopped at the limit fell from 10 of 38 to 2,
    facets found 81% -> 90%, median time 86 s -> 82 s; reads are one page each,
    so a question across several documents needs more than 20."""
    tool_calls: int = 30
    visual_calls: int = 4
    research_seconds: int = 300
    total_seconds: int = 420
    call_seconds: int = 120
    input_tokens: int = 28000
    # Share of the model's context the TP text may use in Search and Direct
    # (agent/tp_text.py). 30% of 128k is about 39k tokens; 0: no TP text.
    tp_context_percent: int = 30
    # Share of the model's context the chat history may use (agent/history.py).
    # In Research, input_tokens is the room for reading on top of it.
    history_context_percent: int = 30

    @classmethod
    def from_config(cls, configs: Mapping[str, str]) -> ResearchLimits:
        """Missing, unreadable or out-of-range values fall back to the default."""
        values = {}
        for key, (name, _, _) in LIMIT_KEYS.items():
            value = limit_value(key, configs.get(key))
            values[name] = DEFAULT_LIMITS[name] if value is None else value
        values['research_seconds'] = min(values['research_seconds'], values['total_seconds'])
        return cls(**values)


# SystemConfig key -> (ResearchLimits field, minimum, maximum).
LIMIT_KEYS = {
    'AGENT_MAX_TOOL_CALLS': ('tool_calls', 1, 40),
    'AGENT_MAX_VISUAL_CALLS': ('visual_calls', 0, 10),
    'AGENT_RESEARCH_SECONDS': ('research_seconds', 5, 1800),
    'AGENT_TOTAL_SECONDS': ('total_seconds', 10, 1800),
    'AGENT_CALL_SECONDS': ('call_seconds', 5, 600),
    'AGENT_MAX_INPUT_TOKENS': ('input_tokens', 4000, 200000),
    'TP_CONTEXT_PERCENT': ('tp_context_percent', 0, 60),
    'HISTORY_CONTEXT_PERCENT': ('history_context_percent', 0, 50),
}
DEFAULT_LIMITS = {f.name: f.default for f in fields(ResearchLimits)}


# Deep research (the student's "deep" switch): more tool calls (up to the
# admin maximum of 40) and twice the time, and a note asking for thoroughness.
# The normal limits stay as they are: speed comes from the model server.
DEEP_EXTRA_TOOL_CALLS = 10
DEEP_NOTE = ('The student asked for deep research. Check every part of the question in the '
             'course, read the pages around the ones you use and the examples they point to, '
             'and prefer a complete answer over a fast one.')


def deep_limits(limits: ResearchLimits) -> ResearchLimits:
    """The limits of a deep research run."""
    total = min(1800, limits.total_seconds * 2)
    return replace(limits, tool_calls=min(40, limits.tool_calls + DEEP_EXTRA_TOOL_CALLS),
                   visual_calls=min(10, limits.visual_calls * 2),
                   research_seconds=min(total, limits.research_seconds * 2), total_seconds=total)


def limit_value(key: str, raw: str | None) -> int | None:
    """The integer an admin value stands for, or None when it is empty or invalid."""
    _, low, high = LIMIT_KEYS[key]
    try:
        value = int((raw or '').strip())
    except ValueError:
        return None
    return value if low <= value <= high else None


# How often a duplicate request checks whether the original run has ended.
DUPLICATE_POLL_SECONDS = 1.0


class _RunningTools(PerCallTools):
    """PerCallTools that refuse every call once the run is no longer running."""

    async def execute(self, name, arguments):
        async with self.session_factory() as db:
            state = await db.scalar(select(AgentRun.status).where(AgentRun.id == self.scope.run_id))
        if state != 'running':
            raise ResearchRunStopped('run_not_running')
        return await super().execute(name, arguments)


# Room kept under the model's context for the answer of a Research call and
# the estimate's error (characters / 3 is low for Chinese).
CONTEXT_RESERVE = 6000


def research_input_tokens(input_tokens: int, history: list[dict] | None,
                          context_tokens: int | None) -> int:
    """The input ceiling of a Research call: the admin's reading room on top of
    the history, never above the model's context minus CONTEXT_RESERVE."""
    wanted = input_tokens + sum(estimate_tokens(m.get('content') or '') for m in history or [])
    return min(wanted, (context_tokens or DEFAULT_CONTEXT) - CONTEXT_RESERVE)


async def run_persisted_research(*, scope: ResearchScope, question: str,
                                 client_request_id: str, model, model_name: str,
                                 attachments: list[dict] | None = None,
                                 version: tuple | None = None,
                                 token_alpha: float, token_beta: float,
                                 history: list[dict] | None = None,
                                 teaching_rules: str = '', research_note: str = '',
                                 earlier_citations: dict[str, dict] | None = None, embed_fn=None,
                                 rerank_fn=None, embed_model: str = '',
                                 rerank_model: str = '', session_factory=AsyncSessionLocal,
                                 tool_factory=None, on_phase=None, on_draft=None, vision_fn=None,
                                 allow_ask: bool = False,
                                 vision_model: str = '',
                                 mode: str = 'research', exam_mode: bool = False,
                                 exam_files: bool = False,
                                 limits: ResearchLimits = ResearchLimits(),
                                 tp_chars: int | None = None, language_model=None,
                                 context_tokens: int | None = None) -> dict:
    """model: agent.model.ResearchModel (or a test double with the same call).
    language_model: the model that names the answer language first
    (agent/language.py, billed like the others); None: no such call, the
    generic language sentence. context_tokens: the model server's context, a
    ceiling for the Research input.
    mode: direct, search or research; exam_mode and exam_files (some files are
    allowed in the exam) only change the direct prompt. The chat endpoint has
    already limited the scope to the allowed files, or chosen direct with an
    empty scope; Direct reads the TP files of the scope only."""
    if mode not in ('direct', 'search', 'research'):
        raise ValueError('unknown_mode')
    runs = ResearchRuns(session_factory)
    usage = ResearchUsage(session_factory)
    started = await runs.begin_run(scope=scope, question=question,
        client_request_id=client_request_id, mode=mode, attachments=attachments, version=version,
        stale_after=max(STALE_RUN_SECONDS, 2 * limits.total_seconds))
    if not started.created:
        # The same request again (a resend, a reconnect): wait for the run
        # that is still working on it rather than report it unfinished.
        status, deadline = started.status, time.monotonic() + limits.total_seconds + 60
        while status in ('running', 'cancelling') and time.monotonic() < deadline:
            await asyncio.sleep(DUPLICATE_POLL_SECONDS)
            status = await runs.get_status(started.run_id)
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
            model=vision_model, input_tokens=estimate,
            max_output_tokens=getattr(vision_fn, 'max_tokens', 1200),
            alpha=token_alpha, beta=token_beta,
            lifetime_seconds=getattr(vision_fn, 'timeout', limits.call_seconds) + 30)
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
    # The run exists from here on: every failure, also before the model is
    # called, must close it, or the session stays blocked.
    try:
        billed = usage.wrap_model(run_id=started.run_id, model=model_name,
                                  alpha=token_alpha, beta=token_beta, call=model)
        answer_language = None
        if language_model is not None:
            try:
                answer_language = await ask_language(
                    usage.wrap_model(run_id=started.run_id, model=model_name, alpha=token_alpha,
                                     beta=token_beta, call=language_model),
                    question, history, teaching_rules)
            except (ResearchQuotaExceeded, ResearchRunStopped):
                raise
            except Exception as exc:
                # The answer still comes, with the generic language sentence.
                failure_log.record('answer_language', type(exc).__name__)
        if mode == 'direct':
            # Direct reads only the TP files of the scope (agent/tp_text.py), no other tool.
            course_tools = CourseTools(tools, max_tool_calls=0, research_seconds=limits.total_seconds,
                                       max_visual_calls=0)
            runner = DirectRunner(billed, course_tools=course_tools, exam_mode=exam_mode,
                                  exam_files=exam_files,
                                  concise=direct_mode.CONCISE,
                                  total_timeout=limits.total_seconds, tp_chars=tp_chars,
                                  model_timeout=limits.call_seconds, on_draft=on_draft)
        elif mode == 'search':
            # Searches and page reads are not research tool calls: no tool budget.
            course_tools = CourseTools(tools, max_tool_calls=40, research_seconds=limits.total_seconds,
                                       max_visual_calls=0,
                                       first_evidence=await runs.next_evidence_number(scope.session_id))
            runner = SearchRunner(course_tools, billed, rewrite=search_mode.REWRITE,
                                  min_score=search_mode.min_score_for(rerank_model), concise=search_mode.CONCISE,
                                  total_timeout=limits.total_seconds, tp_chars=tp_chars,
                                  model_timeout=limits.call_seconds, on_phase=on_phase, on_draft=on_draft)
        else:
            course_tools = CourseTools(tools, max_tool_calls=limits.tool_calls,
                                       research_seconds=limits.research_seconds,
                                       max_visual_calls=limits.visual_calls,
                                       first_evidence=await runs.next_evidence_number(scope.session_id))
            runner = ResearchRunner(course_tools, billed,
                max_tool_calls=limits.tool_calls, research_timeout=limits.research_seconds,
                total_timeout=limits.total_seconds, model_timeout=limits.call_seconds,
                max_input_tokens=research_input_tokens(limits.input_tokens, history, context_tokens),
                lead_min_score=search_mode.min_score_for(rerank_model),
                on_phase=on_phase, on_draft=on_draft)
        result = await runner.run(question, history=history, teaching_rules=teaching_rules,
                                  research_note=research_note, earlier_citations=earlier_citations,
                                  allow_ask=allow_ask, answer_language=answer_language)
        await runs.finish_run(started.run_id, result)
    except asyncio.CancelledError:
        # Both steps in one shielded task: a second cancellation (the server
        # can cancel a closed stream more than once) must not stop the run
        # between 'cancelling' and 'cancelled'.
        async def close_cancelled():
            await runs.request_cancel(started.run_id, scope.user_id)
            await runs.mark_terminal(started.run_id, status='cancelled',
                                     stop_reason='client_cancelled')
        await asyncio.shield(close_cancelled())
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
