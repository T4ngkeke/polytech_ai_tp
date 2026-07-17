"""
routers/chat.py — Streaming chat endpoint with 3-tier rule injection & dynamic LLM config.

Execution Flow
--------------
1. Gate checks
   - Validate session ownership & is_deleted.
   - Verify student membership in the class owning the session's lab.
   - Quota check (UsageStat tokens_used < daily_token_quota).

2. Three-Tier Prompt Controller
   - Fetch session → lab_id → class_id.
   - Query Rules table for active rules:
     * level='class', target_id=class_id
     * level='lab', target_id=lab_id
     * level='student', target_id=user_id
   - Concatenate sequentially into a Master System Prompt.

3. Dynamic LLM Proxy (Zero-Downtime)
   - Fetch LLM_BASE_URL, LLM_API_KEY, LLM_MODEL from SystemConfig table.
   - Fallback to env-based config if DB rows are missing.
   - Instantiate AsyncOpenAI client dynamically per request.

4. SSE streaming + token counting
   - Yield tokens via text/event-stream.
   - Track completion_tokens from yielded chunks or final usage stats.
   - Break early on client disconnect (await request.is_disconnected()).

5. BackgroundTasks DB write (after stream finishes)
   - Insert user message.
   - Insert LLM message with prompt_tokens + completion_tokens.
   - Upsert usage_stats (increment tokens_used + request_count for today).
"""

import json
import logging
import uuid
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.agent.router import _ROUTER_SCHEMA
from backend.app.agent.graph import build_agent
from backend.app.agent.history import trim_history
from backend.app.auth import get_current_user
from backend.app.database import AsyncSessionLocal, get_db
from backend.app.models import (
    Class,
    ClassStudent,
    Document,
    Lab,
    Audience,
    Exercise,
    Message,
    MessageFeedback,
    SenderType,
    Session,
    SystemConfig,
    UsageStat,
    User,
)
from backend.app.schemas import ChatStreamRequest, MessageFeedbackRequest
from backend.app.services import rule_service
from backend.app.services.billing import compute_billed_tokens
from backend.app.services.model_routing import resolve_model_routing
from backend.app.services.trace_service import TraceBuilder

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ---------------------------------------------------------------------------
# Helper: Fetch LLM config from SystemConfig table with env fallback
# ---------------------------------------------------------------------------

async def _get_llm_config(db: AsyncSession) -> dict[str, str]:
    """
    Read LLM connection parameters from the SystemConfig table.

    [v7.2] The model-routing table (embedding / rerank / ingest / router split,
    token weights) is resolved via ``resolve_model_routing``, which applies the
    opt-in fallback to the main LLM. The flat keys (``base_url`` etc.) are kept
    for backward compatibility with existing call sites.
    """
    result = await db.execute(select(SystemConfig))
    configs = {row.key: row.value for row in result.scalars().all()}
    routing = resolve_model_routing(configs)

    return {
        "base_url": routing.llm.base_url,
        "api_key": routing.llm.api_key,
        "model": routing.llm.model,
        # [v7.2] embedding endpoint is now independently configurable.
        "embedding_base_url": routing.embedding.base_url,
        "embedding_api_key": routing.embedding.api_key,
        "embedding_model": routing.embedding.model,
        "rerank_url": routing.rerank.base_url,
        "rerank_api_key": routing.rerank.api_key,
        "rerank_model": routing.rerank.model,
        # [v7.3/v8.0] absolute relevance floor for context injection; empty = off
        # (ships disabled until calibrated). Drives the zero-context disclaimer.
        "rerank_score_threshold": configs.get("RERANK_SCORE_THRESHOLD", ""),
        # [v7.2] router model for the live exercise-number LLM fallback.
        "router_base_url": routing.router.base_url,
        "router_api_key": routing.router.api_key,
        "router_model": routing.router.model,
        "rag_max_retries": configs.get("RAG_MAX_RETRIES", "1"),
        # [v8.1] estimated-token budget for chat history (OOM protection).
        "context_max_tokens": configs.get("CONTEXT_MAX_TOKENS", "8000"),
        "token_alpha": str(routing.token_alpha),
        "token_beta": str(routing.token_beta),
    }


# ---------------------------------------------------------------------------
# Helper: build the SSE citations payload (enrich agent citations with filename)
# ---------------------------------------------------------------------------

async def _build_citations_payload(db: AsyncSession, citations: list[dict]) -> list[dict]:
    """Dedup the agent's citations and enrich each with its document filename.

    The agent emits ``{document_id, page_no}``; students can't resolve a raw
    document_id, so we resolve filenames here (one query) while the request DB
    session is still open. Returns ``[{document_id, filename, page_no}, ...]``.
    """
    if not citations:
        return []

    # Dedup by (document_id, page_no), preserving first-seen order.
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for c in citations:
        key = (c.get("document_id"), c.get("page_no"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)

    # Resolve filenames in a single query.
    uuid_ids = []
    for doc_id in {c.get("document_id") for c in deduped if c.get("document_id")}:
        try:
            uuid_ids.append(uuid.UUID(str(doc_id)))
        except (ValueError, TypeError):
            continue
    filenames: dict[str, str] = {}
    if uuid_ids:
        rows = await db.execute(
            select(Document.id, Document.filename).where(Document.id.in_(uuid_ids))
        )
        filenames = {str(r.id): r.filename for r in rows.all()}

    return [
        {
            "document_id": c.get("document_id"),
            "filename": filenames.get(c.get("document_id")),
            "page_no": c.get("page_no"),
        }
        for c in deduped
    ]


# ---------------------------------------------------------------------------
# Helper: query-embedding function for the agent's RAG node
# ---------------------------------------------------------------------------

def _make_embed_fn(llm_config: dict[str, str]):
    """Build an async embedder hitting the configured embedding model."""
    async def embed(texts: list[str]) -> list[list[float]]:
        client = AsyncOpenAI(
            api_key=llm_config["embedding_api_key"],
            base_url=llm_config["embedding_base_url"],
        )
        # encoding_format="float" is mandatory: the OpenAI SDK otherwise defaults
        # to "base64", which some OpenAI-compatible servers (e.g. Albert) reject
        # with a 500. Float is universally supported.
        resp = await client.embeddings.create(
            model=llm_config["embedding_model"], input=texts, encoding_format="float"
        )
        return [item.embedding for item in resp.data]
    return embed


def _make_rerank_fn(llm_config: dict[str, str]):
    """Build a reranker hitting the configured rerank endpoint, or None if unset.

    Expects an OpenAI-compatible rerank endpoint (TEI / Infinity / vLLM) returning
    `{"results": [{"index": i, "relevance_score": s}, ...]}`. When `RERANK_URL` is
    empty, hybrid retrieval degrades gracefully to fusion-only ordering.
    """
    url = llm_config.get("rerank_url") or ""
    if not url:
        return None

    import httpx

    async def rerank(query: str, documents: list[str]) -> list[float]:  # pragma: no cover
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json={
                "model": llm_config["rerank_model"],
                "query": query,
                "documents": documents,
            })
            resp.raise_for_status()
            results = resp.json().get("results", [])
        scores = [0.0] * len(documents)
        for item in results:
            scores[item["index"]] = item["relevance_score"]
        return scores
    return rerank


def _make_grade_fn(llm_config: dict[str, str]):
    """Guided 3-tier retrieval self-eval: returns 'good' | 'partial' | 'bad'."""
    async def grade(query: str, documents: list[str]) -> str:  # pragma: no cover
        client = AsyncOpenAI(api_key=llm_config["api_key"], base_url=llm_config["base_url"])
        joined = "\n\n".join(f"[{i}] {d}" for i, d in enumerate(documents))
        resp = await client.chat.completions.create(
            model=llm_config["model"],
            messages=[{
                "role": "user",
                "content": (
                    "Rate how well the retrieved material answers the question. "
                    "Reply with exactly one word: good, partial, or bad.\n\n"
                    f"Question: {query}\n\nMaterial:\n{joined}"
                ),
            }],
            max_tokens=4,
        )
        return (resp.choices[0].message.content or "").strip().lower()
    return grade


def _make_rewrite_fn(llm_config: dict[str, str]):
    """Rewrite the query for a better retrieval pass."""
    async def rewrite(query: str) -> str:  # pragma: no cover
        client = AsyncOpenAI(api_key=llm_config["api_key"], base_url=llm_config["base_url"])
        resp = await client.chat.completions.create(
            model=llm_config["model"],
            messages=[{
                "role": "user",
                "content": (
                    "Rewrite this question to retrieve better course material "
                    "(add synonyms / key terms). Reply with the rewritten query only.\n\n"
                    f"{query}"
                ),
            }],
        )
        return (resp.choices[0].message.content or query).strip()
    return rewrite


def _make_router_llm_fn(llm_config: dict[str, str]):
    """[v8.0] The one-call router. `classify()` assembles the messages; this closes
    over the ROUTER_MODEL endpoint + json_schema and returns the raw JSON string.
    The router's tokens are cheap and are not billed to the student's quota."""
    async def router_llm(messages: list[dict]) -> str:  # pragma: no cover
        client = AsyncOpenAI(
            api_key=llm_config["router_api_key"],
            base_url=llm_config["router_base_url"],
        )
        resp = await client.chat.completions.create(
            model=llm_config["router_model"],
            messages=messages,
            max_tokens=200,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "router_decision", "schema": _ROUTER_SCHEMA},
            },
        )
        return resp.choices[0].message.content or ""
    return router_llm


# ---------------------------------------------------------------------------
# Background task: save messages & usage stats
# ---------------------------------------------------------------------------

async def save_chat_background_task(
    user_id: str,
    session_id: str,
    user_message_content: str,
    llm_message_content: str,
    prompt_tokens: int,
    completion_tokens: int,
    token_alpha: float = 1.0,
    token_beta: float = 1.0,
    trace: TraceBuilder | None = None,
):
    """
    Background task to save messages and upsert usage stats.
    Uses its own DB session since the request session is already closed.

    [v7.2] The per-message record keeps the *raw* prompt/completion/total counts,
    but the quota (UsageStat.tokens_used) accumulates *billed* tokens — the
    weighted sum that discounts cheaper prefill tokens.
    """
    total_tokens = prompt_tokens + completion_tokens
    billed_tokens = compute_billed_tokens(
        prompt_tokens, completion_tokens, alpha=token_alpha, beta=token_beta
    )

    async with AsyncSessionLocal() as db:
        try:
            # 1. Insert user message
            user_msg = Message(
                session_id=session_id,
                sender=SenderType.user,
                content=user_message_content,
            )

            # 2. Insert LLM message
            llm_msg = Message(
                session_id=session_id,
                sender=SenderType.llm,
                content=llm_message_content,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                billed_tokens=billed_tokens,
            )
            db.add_all([user_msg, llm_msg])

            # [v8.0] One AgentTraceLog row per message (health panel / self-eval data).
            if trace is not None:
                await db.flush()  # materialize llm_msg.id for the soft reference
                await trace.flush(db, message_id=llm_msg.id)

            # 3. Upsert UsageStat for today
            today = date.today()
            dialect = db.bind.dialect.name

            if dialect == "postgresql":
                from sqlalchemy.dialects.postgresql import insert
            else:
                from sqlalchemy.dialects.sqlite import insert

            stmt = insert(UsageStat).values(
                user_id=user_id,
                date=today,
                tokens_used=billed_tokens,
                request_count=1,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id", "date"],
                set_={
                    "tokens_used": UsageStat.tokens_used + billed_tokens,
                    "request_count": UsageStat.request_count + 1,
                },
            )
            await db.execute(stmt)
            await db.commit()
        except Exception as e:
            logger.error("Background task failed: %s", repr(e), exc_info=True)
            await db.rollback()
            raise


# ===================================================================
# POST /api/chat/stream
# ===================================================================


@router.post("/stream")
async def chat_stream(
    request: Request,
    body: ChatStreamRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Core streaming chat endpoint with 3-tier rule injection and
    dynamic LLM configuration.
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

    # [v8.0] Clarify anti-loop: if the previous turn already asked "which exercise?",
    # a still-unresolved turn falls through to rag instead of clarifying again.
    prev_was_clarify = session.last_route == "clarify"

    # [v8.0] Sticky exercise → the router's dialogue-state inputs (number + a short
    # statement excerpt). It only fills a blank number; it never picks the route.
    sticky_number = None
    sticky_excerpt = None
    if session.current_exercise_id is not None:
        sticky_ex = await db.get(Exercise, session.current_exercise_id)
        if sticky_ex is not None:
            sticky_number = sticky_ex.number_normalized
            sticky_excerpt = (sticky_ex.statement or "")[:100]

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
    if usage and usage.tokens_used >= current_user.daily_token_quota:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Daily token quota exceeded",
        )

    # ---------------------------------------------------------
    # STEP 2: Agent (router → retrieve → synthesize) builds the payload
    # ---------------------------------------------------------
    llm_config = await _get_llm_config(db)

    # Fetch last 20 messages as chronological chat history.
    msg_result = await db.execute(
        select(Message)
        .where(Message.session_id == body.session_id)
        .order_by(Message.created_at.desc())
        .limit(20)
    )
    last_messages = msg_result.scalars().all()
    last_messages.reverse()
    history = [
        {"role": "user" if m.sender == SenderType.user else "assistant", "content": m.content}
        for m in last_messages
    ]
    # [v8.1] Cap the history's estimated token size (the 20-message cap bounds
    # count, not size) — oldest turns are silently forgotten so a marathon
    # conversation can never grow the prompt until the local engine OOMs.
    history = trim_history(
        history, max_tokens=int(llm_config["context_max_tokens"] or 8000)
    )

    rule_texts = await rule_service.get_active_rule_texts(db, class_id, lab_id, current_user.id)

    # [v8.0] Per-request trace: the graph writes route + degradation flags; here we
    # add the injection red-flag and, after the run, recall/all-filtered. Flushed to
    # one AgentTraceLog row in the background task (the health panel's data source).
    trace = TraceBuilder(session_id=body.session_id, lab_id=lab_id)
    trace.mark_if_suspicious(body.message)

    agent = build_agent(
        db,
        embed_fn=_make_embed_fn(llm_config),
        router_llm_fn=_make_router_llm_fn(llm_config),
        rerank_fn=_make_rerank_fn(llm_config),
        grade_fn=_make_grade_fn(llm_config),
        rewrite_fn=_make_rewrite_fn(llm_config),
        max_retries=int(llm_config["rag_max_retries"]),
        router_model_name=llm_config["router_model"],
        rerank_score_threshold=(
            float(llm_config["rerank_score_threshold"])
            if llm_config["rerank_score_threshold"] else None
        ),
        trace=trace,
    )
    agent_result = await agent.ainvoke({
        "message": body.message,
        "class_id": class_id,
        "lab_id": lab_id,
        "user_id": current_user.id,
        "history": history,
        "sticky_number": sticky_number,
        "sticky_excerpt": sticky_excerpt,
        "prev_was_clarify": prev_was_clarify,
        "is_test": session.is_test,
        **rule_texts,
    })
    messages_payload = agent_result["messages_payload"]
    trace.set("recall_count", len(agent_result.get("citations", [])))
    trace.set("all_filtered", bool(agent_result.get("no_material")))

    # [v8.0] Remember this turn's route for the next turn's clarify anti-loop.
    session.last_route = agent_result["route"]
    await db.flush()

    # [v8.0] Sticky write-back: on an exercise hit, remember it on the session so a
    # later numberless follow-up ("and the next part?") reconnects seamlessly.
    if (
        agent_result.get("route") == "exercise"
        and agent_result.get("exercise_number") is not None
        and lab_id
    ):
        matched = await db.execute(
            select(Exercise.id)
            .where(
                Exercise.lab_id == lab_id,
                Exercise.number_normalized == agent_result["exercise_number"],
                Exercise.audience == Audience.student,
            )
            .limit(1)
        )
        ex_id = matched.scalar_one_or_none()
        if ex_id is not None:
            session.current_exercise_id = ex_id
            await db.flush()

    # Resolve citations now, while the request DB session is still open (the
    # streaming generator runs after the request returns and cannot use `db`).
    citations_payload = await _build_citations_payload(
        db, agent_result.get("citations", [])
    )

    # ---------------------------------------------------------
    # STEP 3: Dynamic LLM Proxy (Zero-Downtime)
    # ---------------------------------------------------------
    client = AsyncOpenAI(
        api_key=llm_config["api_key"],
        base_url=llm_config["base_url"],
    )

    try:
        stream = await client.chat.completions.create(
            model=llm_config["model"],
            messages=messages_payload,
            stream=True,
            # [v7.2] Opt into streamed token usage. Without this most OpenAI-
            # compatible servers (vLLM/Ollama) omit `usage` on streamed chunks,
            # so billing would fall back to a flat 10/10 charge and quota would be
            # meaningless. A final usage-only chunk arrives after the content.
            stream_options={"include_usage": True},
            # Disable "thinking/reasoning" mode for reasoning-capable models
            # (e.g. Qwen3): otherwise the model emits all tokens in a non-standard
            # `reasoning` field with an empty `content`, so nothing streams to the
            # client. Ollama's OpenAI-compatible endpoint honors this; plain models
            # simply ignore it.
            extra_body={"reasoning_effort": "low"},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LLM service unavailable: {str(e)}",
        )

    # ---------------------------------------------------------
    # STEP 4: SSE Streaming + Background Task
    # ---------------------------------------------------------
    stream_results = {
        "content": "",
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }

    async def event_generator():
        disconnected = False
        # [v8.0] Open with a status event so the client can show a "working…"
        # indicator; it carries the resolved route. (Per-node progress would need
        # graph.astream_events — deferred.)
        yield f"event: status\ndata: {json.dumps({'route': agent_result['route']})}\n\n"
        try:
            async for chunk in stream:
                if await request.is_disconnected():
                    disconnected = True
                    break

                # Check for token usage
                if chunk.usage:
                    stream_results["prompt_tokens"] = chunk.usage.prompt_tokens
                    stream_results["completion_tokens"] = chunk.usage.completion_tokens

                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        stream_results["content"] += delta.content
                        # JSON-encode the token so embedded newlines survive SSE
                        # framing. A raw `data: foo\nbar\n\n` would split on the
                        # inner newline and the client would lose it, collapsing
                        # multi-line code/markdown into one line (renders only
                        # correctly after a refresh, which reloads the DB copy).
                        yield f"data: {json.dumps(delta.content)}\n\n"

            # After the token stream: emit citations, then a terminal `done`
            # event (the client stops streaming and refreshes quota on `done`).
            if not disconnected:
                yield f"event: citations\ndata: {json.dumps(citations_payload)}\n\n"
                yield "event: done\ndata: {}\n\n"
        except Exception as e:
            # A mid-stream failure must surface as an explicit `error` event so the
            # client can stop the spinner and show a message — otherwise the
            # connection just drops with no `done` and the UI hangs.
            logger.error("Chat stream failed mid-flight: %s", repr(e), exc_info=True)
            yield f"event: error\ndata: {json.dumps({'detail': 'Generation failed'})}\n\n"
        finally:
            # Enqueue the background task with fallback token estimates
            pt = stream_results["prompt_tokens"] or 10
            ct = stream_results["completion_tokens"] or 10

            background_tasks.add_task(
                save_chat_background_task,
                user_id=current_user.id,
                session_id=body.session_id,
                user_message_content=body.message,
                llm_message_content=stream_results["content"],
                prompt_tokens=pt,
                completion_tokens=ct,
                token_alpha=float(llm_config["token_alpha"]),
                token_beta=float(llm_config["token_beta"]),
                trace=trace,
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )


@router.post("/messages/{message_id}/feedback")
async def rate_message(
    message_id: uuid.UUID,
    body: MessageFeedbackRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[v8.0 §11B] Thumbs up/down on an assistant message — a free golden-set
    label (a 👎 links back to the message's AgentTraceLog for review). A student
    may only rate a message in a session they own."""
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
