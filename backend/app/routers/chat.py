"""
routers/chat.py — the streaming chat endpoint.

1. Gate checks: session ownership, class membership (or the owning teacher's
   test drive), an open lab, the tools allowed in the lab, the daily quota.
2. The teacher's rules (class, lab, student) become part of the system prompt.
3. The research agent answers (services/research_service.py). A class in exam
   mode searches only the files its teacher allowed in the exam; with none, the
   agent has no tools at all. The answer is saved with its citations and billed
   call by call before it is sent as server-sent events.

Models and limits come from SystemConfig (admin) with environment fallbacks.
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import replace
from types import MappingProxyType

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.agent.history import history_budget
from backend.app.agent.model import GREEDY, ResearchModel
from backend.app.agent.result import ResearchQuotaExceeded
from backend.app.agent.tools import create_scope
from backend.app.config import settings
from backend.app.auth import get_current_user
from backend.app.database import get_db
from backend.app.models import (
    ChatRefusal,
    CitationOpen,
    Class,
    ClassStudent,
    Lab,
    Message,
    MessageCitation,
    MessageFeedback,
    SenderType,
    Session,
    User,
    UserRole,
)
from backend.app.schemas import ChatStreamRequest, CitationOpenedRequest, CodeResultRequest, MessageFeedbackRequest
from backend.app.services import chat_file_service, feature_service, memory_service, quota_service, rule_service
from backend.app.agent import attachments as agent_attachments
from backend.app.agent import memory as agent_memory
from backend.app.agent import follow_ups as agent_follow_ups
from backend.app.agent import compaction as agent_compaction
from backend.app.routers.teacher_access import get_storage_root
from backend.app.services.model_routing import Endpoint
from backend.app.services.model_servers import context_tokens
from backend.app.services.document_access import DocumentNotFound
from backend.app.services.research_runs import ResearchRuns, RunConflict
from backend.app.services.research_service import DEEP_NOTE, deep_limits, run_persisted_research
from backend.app.agent.web_tools import WebTools
from backend.app.agent import code_tools as agent_code
from backend.app.services.model_clients import (  # noqa: F401  (tests import _get_llm_config from here)
    get_llm_config as _get_llm_config, make_embed_fn as _make_embed_fn, make_rerank_fn as _make_rerank_fn,
)
from backend.app.services.research_vision import ResearchVision
from backend.app.services.session_titles import title_first_exchange

logger = logging.getLogger(__name__)
# Background memory reviews still running (a reference keeps each task alive).
_memory_reviews: set[asyncio.Task] = set()

# Seconds without an event before the research stream sends an SSE comment
# (proxies such as nginx close a stream after 60 s of silence by default).
KEEPALIVE_SECONDS = 15
# Messages read for the history; its size limit (agent/history.py) decides
# how many are sent.
HISTORY_MESSAGES = 400


def language_model(agent: Endpoint):
    """The model that names the answer language before each answer
    (agent/language.py): the agent, without sampling. Tests replace it."""
    return ResearchModel(agent, sampling=GREEDY)


def _history_budget(context: int | None, limits) -> int:
    """Estimated tokens of history a question may carry."""
    return history_budget(context, limits.history_context_percent)


def _research_frames(items):
    """SSE frames for queued research events: a phase name is a status event;
    ('draft', text) adds draft text, ('draft', None) withdraws the draft;
    ('tasks', list) is the model's task checklist as it is now; ('code', {id, code})
    asks the browser to run Python (agent/code_tools.py); ('step', {...}) is
    what one tool call did (runner.step_of), for the process line;
    ('question', {user_message_id}) is the saved question's id.
    Consecutive draft text goes out as one frame."""
    frames, text = [], ''
    for item in [*items, None]:
        if isinstance(item, tuple) and item[0] in ('tasks', 'code', 'step', 'question'):
            if text:
                frames.append(f"event: draft\ndata: {json.dumps(text)}\n\n")
                text = ''
            frames.append(f"event: {item[0]}\ndata: {json.dumps(item[1], ensure_ascii=False)}\n\n")
            continue
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

# The answer being written in each conversation, by this process (uvicorn runs
# one worker): another window of the same student asks whether one is running
# (GET /sessions/{id}/state) and may stop it (POST /sessions/{id}/stop).
_running: dict[uuid.UUID, asyncio.Task] = {}


async def _refuse(db: AsyncSession, user: User, lab_id, reason: str, code: int, detail: str):
    """Refuse a question before it starts, and count it for monitoring."""
    db.add(ChatRefusal(user_id=user.id, lab_id=lab_id, reason=reason))
    await db.commit()
    raise HTTPException(status_code=code, detail=detail)


# ===================================================================
# POST /api/chat/stream
# ===================================================================


@router.post("/stream")
async def chat_stream(
    request: Request,
    body: ChatStreamRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    The answer of the research agent, as server-sent events.
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
    attachments: list[dict] = []

    if lab_id:
        # The lab must exist, be active, and not be soft-deleted. A locked
        # or deleted lab is read-only and must reject chat.
        lab_result = await db.execute(
            select(Lab).where(
                Lab.id == lab_id,
                Lab.is_active.is_(True),
                Lab.is_deleted.is_(False),
            )
        )
        lab = lab_result.scalar_one_or_none()
        if lab is None:
            await _refuse(db, current_user, lab_id, 'lab_locked', status.HTTP_403_FORBIDDEN,
                          "This lab is locked or no longer available")
        class_id = lab.class_id

        # The owning teacher's test-drive session bypasses the student
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
                await _refuse(db, current_user, lab_id, 'not_member', status.HTTP_403_FORBIDDEN,
                              "You are not a member of the class that owns this lab")

        # The tools turned on for this question must be allowed in this lab
        # (class switches, the lab's own choices, exam mode: feature_service),
        # and so must what it attaches (chat_file_service.resolve_attachments).
        if body.tools or body.attachments:
            class_features, class_exam = (await db.execute(
                select(Class.features, Class.exam_mode).where(Class.id == class_id))).one()
            allowed = feature_service.effective(class_features, lab.feature_overrides,
                                                exam_mode=bool(class_exam))
            if not all(allowed[tool] for tool in body.tools):
                await _refuse(db, current_user, lab_id, 'feature_off', status.HTTP_403_FORBIDDEN,
                              'feature_off')
            try:
                attachments = await chat_file_service.resolve_attachments(
                    db, body.attachments, user_id=current_user.id, session_id=session.id,
                    lab_id=lab_id, class_id=class_id, exam_mode=bool(class_exam), allowed=allowed)
            except chat_file_service.AttachmentError as exc:
                await _refuse(db, current_user, lab_id, exc.code, exc.status, exc.code)

    # A new version of the latest question replaces it and everything after it
    # (its answer); those messages stay for the audit (research_runs.begin_run).
    version = None
    replaced: list[uuid.UUID] = []
    if body.version_of is not None:
        target = await db.get(Message, body.version_of)
        if (target is None or target.session_id != session.id or target.sender != SenderType.user
                or target.superseded_at is not None):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "message_not_found")
        later_question = await db.scalar(select(Message.id).where(
            Message.session_id == session.id, Message.sender == SenderType.user,
            Message.superseded_at.is_(None), Message.created_at > target.created_at).limit(1))
        if later_question is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "not_latest_question")
        replaced = list((await db.scalars(select(Message.id).where(
            Message.session_id == session.id, Message.superseded_at.is_(None),
            Message.created_at >= target.created_at))).all())
        version = (target.version_of or target.id, replaced)

    # The quota is checked here only: a question that starts always finishes,
    # even when it goes over; the next one is refused (quota_service).
    blocked = (await quota_service.usage(db, current_user)).blocked
    if blocked:
        await _refuse(db, current_user, lab_id, f'{blocked}_quota', status.HTTP_429_TOO_MANY_REQUESTS,
                      f'{blocked}_quota_reached')

    # ---------------------------------------------------------
    # STEP 2: the research agent (agent/runner.py) answers
    # ---------------------------------------------------------
    llm_config = await _get_llm_config(db)

    rule_texts = await rule_service.get_active_rule_texts(db, class_id, lab_id, current_user.id)

    # The research agent answers every chat.
    if not settings.AGENTIC_SEARCH_ENABLED:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "chat_not_enabled")
    if 'web' in body.tools and not settings.WEB_SEARCH_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "web_search_not_configured")
    if lab_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "session_without_lab")
    # All source scope and focus IDs are resolved from the authenticated
    # session. The model never supplies a URL or a revision map.
    # A class in exam mode searches only the files allowed in the exam; with
    # none, the agent has no tools at all (runner EXAM_NO_FILES_TURN).
    exam_mode = bool(await db.scalar(select(Class.exam_mode).where(Class.id == class_id)))
    # The research model is the AGENT_* endpoint (default: the main LLM);
    # view_image uses the VLM_* endpoint and is off when no VLM_MODEL is set.
    agent = Endpoint(llm_config['agent_base_url'],
                     llm_config['agent_api_key'], llm_config['agent_model'])
    if not agent.model or not agent.base_url:
        raise HTTPException(503, 'agent_model_not_configured')
    # The history may use a share of the model's context (agent/history.py).
    context = await context_tokens(agent.base_url, agent.model, agent.api_key)
    limits = llm_config['research_limits']
    try:
        scope = await create_scope(db, user_id=current_user.id,
                                   session_id=body.session_id, exam_only=exam_mode)
        exam_files = exam_mode and bool(scope.snapshot_revisions)
        exam_no_files = exam_mode and not exam_files
        # The tools below are off in exam mode: the gate above has refused them
        # (feature_service), so these switches never meet exam_no_files.
        # Deep research: the research loop with more room (research_service.deep_limits).
        deep = 'deep' in body.tools
        if deep:
            limits = deep_limits(limits)
        # Web search: the research loop gets search_web and fetch_url (agent/web_tools.py).
        web = 'web' in body.tools
        # Python in the browser: the research loop gets execute_code (agent/code_tools.py).
        run_code = 'code' in body.tools
        # Attachments (agent/attachments.py): an attached course file limits
        # this question's course search to it.
        full_scope = scope
        if attachments:
            attached_docs = {a['id'] for a in attachments if a['kind'] == 'document'}
            if attached_docs:
                scope = replace(scope, snapshot_revisions=MappingProxyType({
                    d: r for d, r in scope.snapshot_revisions.items() if str(d) in attached_docs}))
        research_factory = async_sessionmaker(db.bind, expire_on_commit=False)
        runs = ResearchRuns(research_factory)

        async def earlier_chat(session_id):
            # An attached chat of this lab: its safe history, as for this chat.
            history, _, _ = await runs.load_context(replace(full_scope, session_id=session_id),
                                                    limit=HISTORY_MESSAGES)
            return history
        attachment_block, images, _ = await agent_attachments.build(
            db, attachments, storage_root=get_storage_root(),
            earlier_chat=earlier_chat)
        # Only what fits the history limit is sent, with the citations of
        # those answers only.
        # Past the limit, a summary of the older part goes in front of the
        # history (agent/compaction.py). None in exam mode, nor when an attached
        # course file narrows the course files (the summary belongs to all of them).
        narrowed = any(a['kind'] == 'document' for a in attachments or ())
        compact = settings.CONTEXT_COMPACTION_ENABLED and not exam_mode and not narrowed
        # The summary itself is written inside the stream (prepare below): it is
        # a model call, and the stream's keep-alives must cover it.
        loaded, earlier_citations, _ = await runs.load_context(
            scope, limit=HISTORY_MESSAGES, max_tokens=_history_budget(context, limits),
            exclude_ids=replaced, compact=compact)
        # Right after a question back the model must answer, not ask again.
        allow_ask = not await runs.last_answer_asked(scope.session_id, exclude_ids=replaced)
    except DocumentNotFound:
        raise HTTPException(404, 'source_unavailable') from None
    teaching_rules = rule_service.teaching_rules_text(rule_texts)
    if exam_files:
        teaching_rules += ('\n\n[EXAM MODE]\nThe class is in exam mode: only the course files the '
                           'teacher allowed for the exam are available. Use only these.')
    embedder = _make_embed_fn(llm_config)
    async def embed_query(query: str):
        return (await embedder([query]))[0]
    # The student's notes of this class (agent/memory.py): never in exam mode,
    # in a test drive, or when the student turned memory off.
    memory_on = (current_user.memory_enabled and current_user.role == UserRole.student
                 and not exam_mode and not session.is_test)
    # "About me" (the student's settings): with the notes, under the same
    # conditions except the memory switch, which is about the notes only.
    about = (agent_memory.about_context(current_user.about_me)
             if (current_user.role == UserRole.student and not exam_mode and not session.is_test)
             else '')
    user_id = current_user.id
    snapshot = {str(k): str(v) for k, v in scope.snapshot_revisions.items()}

    async def prepare() -> tuple[list[dict], str]:
        """The history to send (with the summary of its older part) and the
        student's notes. Both may call a model, so they run inside the stream."""
        history = loaded
        if compact:
            history, _ = await agent_compaction.apply(
                research_factory, loaded, ResearchModel(agent) if loaded.dropped else None, snapshot)
        notes_block = ''
        if memory_on:
            try:
                async with research_factory() as notes_db:
                    notes = await memory_service.for_recall(notes_db, user_id, class_id)
                vector = None
                if not agent_memory.all_fit(notes):
                    vector = await embed_query(agent_memory.recall_query(history, body.message))
                notes_block = agent_memory.memory_context(notes, vector)
            except Exception as exc:              # a question never fails for its notes
                logger.warning('Memory recall failed (%s)', type(exc).__name__)
        return history, '\n\n'.join(b for b in (about, notes_block) if b)
    vlm = Endpoint(llm_config['vlm_base_url'],
                   llm_config['vlm_api_key'], llm_config['vlm_model'])
    vision = (ResearchVision(vlm, timeout=llm_config['research_limits'].call_seconds)
              if vlm.model and vlm.base_url else None)
    request_id = str(body.client_request_id or uuid.uuid4())
    title_model = ResearchModel(agent)

    async def research_events():
        queue = asyncio.Queue()
        def phase(name):
            if name in {'finding', 'reading', 'checking_image', 'checking_sources', 'searching_web',
                        'running_code'}:
                queue.put_nowait(name)

        def draft(text):
            # None withdraws the draft shown so far.
            queue.put_nowait(('draft', text))

        def tasks(items):
            queue.put_nowait(('tasks', items))

        def step(item):
            queue.put_nowait(('step', item))

        def started(question_id):
            queue.put_nowait(('question', {'user_message_id': question_id}))

        async def save_code_image(data: bytes, n: int) -> str | None:
            # An image a Python run printed: a chat file of this conversation.
            try:
                async with research_factory() as file_db:
                    row = await chat_file_service.save(
                        file_db, root=get_storage_root(), user_id=current_user.id, lab_id=lab_id,
                        filename=f'python-{n}.png', data=data)
                    row.session_id = scope.session_id
                    await file_db.commit()
                    return str(row.id)
            except Exception as exc:
                logger.warning('Code output image not saved (%s)', type(exc).__name__)
                return None
        code_tools = (agent_code.CodeTools(current_user.id, lambda event: queue.put_nowait(('code', event)),
                                           save_image=save_code_image) if run_code else None)
        yield f"event: status\ndata: {json.dumps({'route': 'agent', 'phase': 'thinking', 'exam_mode': exam_mode})}\n\n"
        preparing = asyncio.create_task(prepare())
        try:
            while not preparing.done():
                done, _ = await asyncio.wait({preparing}, timeout=KEEPALIVE_SECONDS)
                if not done:
                    if await request.is_disconnected():
                        return
                    yield ": keep-alive\n\n"
            safe_history, memory_block = preparing.result()
        except Exception:
            logger.exception('Research request failed')
            yield f"event: error\ndata: {json.dumps({'detail': 'Research failed'})}\n\n"
            return
        finally:
            if not preparing.done():
                preparing.cancel()
        task = asyncio.create_task(run_persisted_research(
            scope=scope, question=body.message, client_request_id=request_id,
            attachments=attachments, version=version, model=ResearchModel(agent), model_name=agent.model,
            token_alpha=float(llm_config['token_alpha']),
            token_beta=float(llm_config['token_beta']),
            history=safe_history, earlier_citations=earlier_citations,
            teaching_rules=teaching_rules, research_note=DEEP_NOTE if deep else '',
            memory_context=memory_block, attachment_context=attachment_block, images=images,
            web_tools=WebTools(settings.WEB_SEARCH_URL) if web else None, code_tools=code_tools,
            embed_fn=embed_query,
            rerank_fn=_make_rerank_fn(llm_config),
            embed_model=llm_config['embedding_model'],
            rerank_model=llm_config['rerank_model'], on_phase=phase, on_tasks=tasks, on_step=step, on_started=started,
            vision_fn=vision, vision_model=vlm.model, limits=limits,
            language_model=language_model(agent), context_tokens=context,
            on_draft=draft, allow_ask=allow_ask,
            exam_no_files=exam_no_files, session_factory=research_factory))
        # A second request while one is answering is refused (RunConflict below):
        # it must not take the place of the answer being written.
        current_run = _running.get(scope.session_id)
        if current_run is None or current_run.done():
            _running[scope.session_id] = task
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
            if task.cancelled():
                # Stopped from another window of this conversation.
                yield f"event: error\ndata: {json.dumps({'detail': 'The answer was stopped', 'code': 'stopped'})}\n\n"
                return
            result = await task
            if result['status'] != 'finished':
                yield f"event: error\ndata: {json.dumps({'detail': 'Research did not finish'})}\n\n"
                return
            # Commit and reload are complete before any answer bytes leave.
            yield f"data: {json.dumps(result['answer'])}\n\n"
            if result.get('clarification'):
                yield f"event: clarification\ndata: {json.dumps(result['clarification'])}\n\n"
            yield f"event: citations\ndata: {json.dumps(result['citations'])}\n\n"
            # The question's id too: the page shows it under a temporary id.
            yield f"event: done\ndata: {json.dumps({'message_id': result['message_id'], 'user_message_id': result.get('user_message_id'), 'course_use': result.get('course_use'), 'web_sources': result.get('web_sources'), 'code_runs': result.get('code_runs')}, ensure_ascii=False)}\n\n"
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
            if memory_on and result.get('answer'):
                turns = 1 + sum(1 for m in safe_history if m['role'] == 'user')
                if agent_memory.due(turns):
                    review = asyncio.create_task(agent_memory.review(
                        research_factory, user_id=current_user.id, class_id=class_id,
                        session_id=scope.session_id, model=title_model, embed=embedder,
                        messages=[*safe_history, {'role': 'user', 'content': body.message},
                                  {'role': 'assistant', 'content': result['answer']}]))
                    _memory_reviews.add(review)
                    review.add_done_callback(_memory_reviews.discard)
            # Suggested next questions (agent/follow_ups.py): the last thing sent.
            # Like the title, a failure here must not follow a delivered answer.
            suggestions = None
            if not exam_mode and not result.get('clarification') and result.get('message_id'):
                try:
                    suggestions = await agent_follow_ups.generate(
                        title_model, [*safe_history, {'role': 'user', 'content': body.message},
                                      {'role': 'assistant', 'content': result['answer']}],
                        teaching_rules=teaching_rules)
                    if suggestions:
                        async with research_factory() as fdb:
                            await fdb.execute(update(Message).where(Message.id == uuid.UUID(str(result['message_id'])))
                                              .values(follow_ups=suggestions))
                            await fdb.commit()
                except Exception:
                    logger.exception('Follow-up suggestions failed')
                    suggestions = None
            if suggestions:
                yield (f"event: follow_ups\ndata: {json.dumps({'message_id': str(result['message_id']), 'follow_ups': suggestions}, ensure_ascii=False)}\n\n")
        except RunConflict as exc:
            # code: the page explains it in the student's language (busy: an
            # answer is being written in another window; nothing was saved).
            replaced = str(exc) == 'version_conflict'
            detail = ('This question was already replaced by a newer version' if replaced
                      else 'Another research request is already running')
            yield f"event: error\ndata: {json.dumps({'detail': detail, 'code': 'replaced' if replaced else 'busy'})}\n\n"
        except ResearchQuotaExceeded:
            yield f"event: error\ndata: {json.dumps({'detail': 'Daily token quota exceeded'})}\n\n"
        except Exception:
            logger.exception('Research request failed')
            yield f"event: error\ndata: {json.dumps({'detail': 'Research failed'})}\n\n"
        finally:
            if _running.get(scope.session_id) is task:
                del _running[scope.session_id]
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
    return StreamingResponse(research_events(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-store',
                                      'X-Accel-Buffering': 'no'})


async def _own_session(db: AsyncSession, session_id: uuid.UUID, user: User) -> Session:
    session = (await db.execute(select(Session).where(
        Session.id == session_id, Session.user_id == user.id,
        Session.is_deleted.is_(False)))).scalar_one_or_none()
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return session


@router.get("/sessions/{session_id}/state")
async def session_state(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """For a page that shows this conversation (another window may ask too):
    whether an answer is being written, how many current questions it has and
    the last one's id (a page that shows something else loads it again)."""
    await _own_session(db, session_id, current_user)
    task = _running.get(session_id)
    current = (Message.session_id == session_id, Message.sender == SenderType.user,
               Message.superseded_at.is_(None))
    questions = await db.scalar(select(func.count()).select_from(Message).where(*current))
    last = await db.scalar(select(Message.id).where(*current).order_by(Message.created_at.desc()).limit(1))
    return {"running": task is not None and not task.done(), "questions": questions or 0,
            "last_question_id": str(last) if last else None}


@router.post("/sessions/{session_id}/stop")
async def stop_session_answer(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Stop the answer being written in this conversation, from any window.
    The run ends as when its own window stops it (cancelled, nothing saved
    for the answer); that window is told with an error event, code 'stopped'."""
    await _own_session(db, session_id, current_user)
    task = _running.get(session_id)
    if task is None or task.done():
        return {"stopped": False}
    task.cancel()
    return {"stopped": True}


@router.get("/sessions/{session_id}/history-start")
async def history_start_of_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Where the history of the next question would start, for the line the
    chat page draws: ``seen_from`` is the first message still sent (None when
    none is), ``hidden`` how many earlier messages no longer are. The same rule
    as /stream (the safe history, then agent/history.history_start)."""
    session = (await db.execute(select(Session).where(
        Session.id == session_id, Session.user_id == current_user.id,
        Session.is_deleted.is_(False)))).scalar_one_or_none()
    if session is None or session.lab_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    exam_mode = bool(await db.scalar(select(Class.exam_mode).join(Lab, Lab.class_id == Class.id)
                                     .where(Lab.id == session.lab_id)))
    llm_config = await _get_llm_config(db)
    agent = Endpoint(llm_config['agent_base_url'], llm_config['agent_api_key'], llm_config['agent_model'])
    context = await context_tokens(agent.base_url, agent.model, agent.api_key) if agent.base_url else None
    try:
        scope = await create_scope(db, user_id=current_user.id, session_id=session_id,
                                   exam_only=exam_mode)
        runs = ResearchRuns(async_sessionmaker(db.bind, expire_on_commit=False))
        history, _, hidden = await runs.load_context(
            scope, limit=HISTORY_MESSAGES,
            max_tokens=_history_budget(context, llm_config['research_limits']))
    except DocumentNotFound:
        raise HTTPException(404, 'source_unavailable') from None
    if not hidden:
        return {"seen_from": None, "hidden": 0}
    return {"seen_from": history[0]['id'] if history else None, "hidden": hidden}


@router.post("/code/{call_id}", status_code=204)
async def code_result(
    call_id: str,
    body: CodeResultRequest,
    current_user: User = Depends(get_current_user),
) -> None:
    """The browser's output of one Python run (agent/code_tools.py): only the
    person whose question asked for it, while the run still waits."""
    if not agent_code.deliver(call_id, current_user.id, body.model_dump()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "code_run_not_waiting")


@router.post("/messages/{message_id}/citation-opened", status_code=204)
async def citation_opened(
    message_id: uuid.UUID,
    body: CitationOpenedRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """The page opened a cited page of an answer: one row in citation_opens.
    Only for an answer of the user's own session that cites that file."""
    cited = await db.scalar(
        select(MessageCitation.id)
        .join(Message, Message.id == MessageCitation.message_id)
        .join(Session, Session.id == Message.session_id)
        .where(MessageCitation.message_id == message_id, MessageCitation.document_id == body.document_id,
               Session.user_id == current_user.id)
        .limit(1))
    if cited is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "citation_not_found")
    db.add(CitationOpen(user_id=current_user.id, message_id=message_id,
                        document_id=body.document_id, page_no=body.page_no))
    await db.commit()


@router.post("/messages/{message_id}/feedback")
async def rate_message(
    message_id: uuid.UUID,
    body: MessageFeedbackRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
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
