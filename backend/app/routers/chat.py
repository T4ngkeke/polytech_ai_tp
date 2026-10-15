"""
routers/chat.py — the streaming chat endpoint.

1. Gate checks: session ownership, class membership (or the owning teacher's
   test drive), an open lab, the daily quota.
2. The teacher's rules (class, lab, student) become part of the system prompt.
3. The research agent answers in the requested mode — direct, search or
   research (services/research_service.py); a class in exam mode answers in
   direct mode only. The answer is saved with its citations and billed call by
   call before it is sent as server-sent events.

Models and limits come from SystemConfig (admin) with environment fallbacks.
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.agent.history import trim_history
from backend.app.agent.model import ResearchModel
from backend.app.agent.result import ResearchQuotaExceeded
from backend.app.agent.tools import create_scope
from backend.app.config import settings
from backend.app.auth import get_current_user
from backend.app.database import get_db
from backend.app.models import (
    Class,
    ClassStudent,
    Lab,
    Message,
    MessageFeedback,
    Session,
    UsageStat,
    User,
)
from backend.app.schemas import ChatStreamRequest, MessageFeedbackRequest
from backend.app.services import quota_service, rule_service
from backend.app.services.model_routing import Endpoint
from backend.app.services.document_access import DocumentNotFound
from backend.app.services.research_runs import ResearchRuns, RunConflict
from backend.app.services.research_service import run_persisted_research
from backend.app.services.model_clients import (  # noqa: F401  (tests import _get_llm_config from here)
    get_llm_config as _get_llm_config, make_embed_fn as _make_embed_fn, make_rerank_fn as _make_rerank_fn,
)
from backend.app.services.research_vision import ResearchVision
from backend.app.services.session_titles import title_first_exchange

logger = logging.getLogger(__name__)

# Seconds without an event before the research stream sends an SSE comment
# (proxies such as nginx close a stream after 60 s of silence by default).
KEEPALIVE_SECONDS = 15


def _research_frames(items):
    """SSE frames for queued research events: a phase name is a status event;
    ('draft', text) adds draft text, ('draft', None) withdraws the draft.
    Consecutive draft text goes out as one frame."""
    frames, text = [], ''
    for item in [*items, None]:
        if isinstance(item, tuple) and item[1] is not None:
            text += item[1]
            continue
        if text:
            frames.append(f"event: draft\ndata: {json.dumps(text)}\n\n")
            text = ''
        if isinstance(item, tuple):
            frames.append("event: draft_reset\ndata: {}\n\n")
        elif item is not None:
            frames.append(f"event: status\ndata: {json.dumps({'route': 'agent', 'phase': item})}\n\n")
    return frames

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ===================================================================
# POST /api/chat/stream
# ===================================================================


@router.post("/stream")
async def chat_stream(
    request: Request,
    body: ChatStreamRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    The answer of the research agent, in the requested mode, as server-sent events.
    """
    # ---------------------------------------------------------
    # STEP 1: Gate checks
    # ---------------------------------------------------------
    # Verify session ownership
    result = await db.execute(
        select(Session).where(
            Session.id == body.session_id,
            Session.user_id == current_user.id,
            Session.is_deleted.is_(False),
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    # Resolve lab → class for rule injection and membership check
    class_id = None
    lab_id = session.lab_id

    if lab_id:
        # [v7.2] The lab must exist, be active, and not be soft-deleted — a locked
        # or deleted lab is read-only and must reject chat (README §6 step 1).
        lab_result = await db.execute(
            select(Lab).where(
                Lab.id == lab_id,
                Lab.is_active.is_(True),
                Lab.is_deleted.is_(False),
            )
        )
        lab = lab_result.scalar_one_or_none()
        if lab is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This lab is locked or no longer available",
            )
        class_id = lab.class_id

        # [v8.0 §11A] The owning teacher's test-drive session bypasses the student
        # membership check (they aren't enrolled in their own class).
        is_owner_test_drive = False
        if session.is_test:
            owner_id = (await db.execute(
                select(Class.teacher_id).where(Class.id == class_id)
            )).scalar_one_or_none()
            is_owner_test_drive = owner_id == current_user.id

        if not is_owner_test_drive:
            # Verify student membership in the class
            membership = await db.execute(
                select(ClassStudent).where(
                    ClassStudent.class_id == class_id,
                    ClassStudent.student_id == current_user.id,
                )
            )
            if membership.scalar_one_or_none() is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You are not a member of the class that owns this lab",
                )

    # Check quota
    today = date.today()
    usage_result = await db.execute(
        select(UsageStat).where(
            UsageStat.user_id == current_user.id,
            UsageStat.date == today,
        )
    )
    usage = usage_result.scalar_one_or_none()
    # The limit includes what a teacher granted today (quota_service).
    if usage and usage.tokens_used >= await quota_service.daily_limit(db, current_user, today):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Daily token quota exceeded",
        )

    # ---------------------------------------------------------
    # STEP 2: the research agent (agent/runner.py, search.py, direct.py) answers
    # ---------------------------------------------------------
    llm_config = await _get_llm_config(db)

    rule_texts = await rule_service.get_active_rule_texts(db, class_id, lab_id, current_user.id)

    # The research agent answers every chat (direct, search or research mode).
    if not settings.AGENTIC_SEARCH_ENABLED:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "chat_not_enabled")
    if lab_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "session_without_lab")
    # All source scope and focus IDs are resolved from the authenticated
    # session. The model never supplies a URL or a revision map.
    # A class in exam mode answers in direct mode only, whatever the client asks.
    exam_mode = bool(await db.scalar(select(Class.exam_mode).where(Class.id == class_id)))
    mode = 'direct' if exam_mode else body.mode
    try:
        scope = await create_scope(db, user_id=current_user.id,
                                   session_id=body.session_id)
        research_factory = async_sessionmaker(db.bind, expire_on_commit=False)
        runs = ResearchRuns(research_factory)
        # History may take a quarter of the research input tokens.
        limits = llm_config['research_limits']
        safe_history, earlier_citations = await runs.load_safe_context(scope)
        # Right after a question back the model must answer, not ask again.
        allow_ask = not await runs.last_answer_asked(scope.session_id)
        safe_history = trim_history(safe_history,
            max_tokens=min(int(llm_config["context_max_tokens"] or 8000),
                           limits.input_tokens // 4))
    except DocumentNotFound:
        raise HTTPException(404, 'source_unavailable') from None
    teaching_rules = '\n\n'.join(
        f'[{label.upper()} RULES]\n{rule_texts[key]}'
        for key, label in (('class_rules', 'class'), ('lab_rules', 'lab'),
                           ('student_rules', 'student')) if rule_texts.get(key))
    embedder = _make_embed_fn(llm_config)
    async def embed_query(query: str):
        return (await embedder([query]))[0]
    # The research model is the AGENT_* endpoint (default: the main LLM);
    # view_image uses the VLM_* endpoint and is off when no VLM_MODEL is set.
    agent = Endpoint(llm_config['agent_base_url'],
                     llm_config['agent_api_key'], llm_config['agent_model'])
    if not agent.model or not agent.base_url:
        raise HTTPException(503, 'agent_model_not_configured')
    vlm = Endpoint(llm_config['vlm_base_url'],
                   llm_config['vlm_api_key'], llm_config['vlm_model'])
    vision = (ResearchVision(vlm, timeout=llm_config['research_limits'].call_seconds)
              if vlm.model and vlm.base_url else None)
    request_id = str(body.client_request_id or uuid.uuid4())
    title_model = ResearchModel(agent)

    async def research_events():
        queue = asyncio.Queue()
        def phase(name):
            if name in {'finding', 'reading', 'checking_image', 'checking_sources'}:
                queue.put_nowait(name)

        def draft(text):
            # None withdraws the draft shown so far.
            queue.put_nowait(('draft', text))
        yield f"event: status\ndata: {json.dumps({'route': 'agent', 'phase': 'thinking', 'mode': mode, 'exam_mode': exam_mode})}\n\n"
        task = asyncio.create_task(run_persisted_research(
            scope=scope, question=body.message, client_request_id=request_id,
            model=ResearchModel(agent), model_name=agent.model,
            token_alpha=float(llm_config['token_alpha']),
            token_beta=float(llm_config['token_beta']),
            history=safe_history, earlier_citations=earlier_citations,
            teaching_rules=teaching_rules,
            embed_fn=embed_query,
            rerank_fn=_make_rerank_fn(llm_config),
            embed_model=llm_config['embedding_model'],
            rerank_model=llm_config['rerank_model'], on_phase=phase,
            vision_fn=vision, vision_model=vlm.model, limits=limits,
            on_draft=draft, allow_ask=allow_ask and mode == 'research',
            mode=mode, exam_mode=exam_mode, session_factory=research_factory))
        last_sent = time.monotonic()
        try:
            while not task.done():
                if await request.is_disconnected():
                    return  # finally cancels the run
                try:
                    items = [await asyncio.wait_for(queue.get(), timeout=0.5)]
                    while not queue.empty():
                        items.append(queue.get_nowait())
                    for frame in _research_frames(items):
                        yield frame
                    last_sent = time.monotonic()
                except asyncio.TimeoutError:
                    # A model call can be silent for minutes; an SSE comment
                    # keeps proxies from closing the stream. Clients ignore it.
                    if time.monotonic() - last_sent >= KEEPALIVE_SECONDS:
                        yield ": keep-alive\n\n"
                        last_sent = time.monotonic()
            result = await task
            if result['status'] != 'finished':
                yield f"event: error\ndata: {json.dumps({'detail': 'Research did not finish'})}\n\n"
                return
            # Commit and reload are complete before any answer bytes leave.
            yield f"data: {json.dumps(result['answer'])}\n\n"
            if result.get('clarification'):
                yield f"event: clarification\ndata: {json.dumps(result['clarification'])}\n\n"
            yield f"event: citations\ndata: {json.dumps(result['citations'])}\n\n"
            yield f"event: done\ndata: {json.dumps({'message_id': result['message_id']})}\n\n"
            # After the answer is final: a new conversation gets its title.
            # A failure here must not turn a delivered answer into an error.
            try:
                title = await title_first_exchange(research_factory, scope.session_id, title_model,
                                                   body.message, result['answer'])
            except Exception:
                logger.exception('Session title failed')
                title = None
            if title:
                yield f"event: title\ndata: {json.dumps({'session_id': str(scope.session_id), 'title': title})}\n\n"
        except RunConflict:
            yield f"event: error\ndata: {json.dumps({'detail': 'Another research request is already running'})}\n\n"
        except ResearchQuotaExceeded:
            yield f"event: error\ndata: {json.dumps({'detail': 'Daily token quota exceeded'})}\n\n"
        except Exception:
            logger.exception('Research request failed')
            yield f"event: error\ndata: {json.dumps({'detail': 'Research failed'})}\n\n"
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
    return StreamingResponse(research_events(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-store',
                                      'X-Accel-Buffering': 'no'})


@router.post("/messages/{message_id}/feedback")
async def rate_message(
    message_id: uuid.UUID,
    body: MessageFeedbackRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Thumbs up/down on an assistant message. A student may only rate a
    message in a session they own."""
    message = (await db.execute(
        select(Message)
        .join(Session, Message.session_id == Session.id)
        .where(Message.id == message_id, Session.user_id == current_user.id)
    )).scalar_one_or_none()
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found"
        )
    message.feedback = MessageFeedback(body.feedback.value)
    await db.commit()
    return {"message_id": message_id, "feedback": body.feedback.value}
