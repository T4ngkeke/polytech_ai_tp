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
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.agent.graph import build_agent
from backend.app.auth import get_current_user
from backend.app.config import settings
from backend.app.database import AsyncSessionLocal, get_db
from backend.app.models import (
    ClassStudent,
    Lab,
    Message,
    Rule,
    RuleLevel,
    SenderType,
    Session,
    SystemConfig,
    UsageStat,
    User,
)
from backend.app.schemas import ChatStreamRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ---------------------------------------------------------------------------
# Helper: Fetch LLM config from SystemConfig table with env fallback
# ---------------------------------------------------------------------------

async def _get_llm_config(db: AsyncSession) -> dict[str, str]:
    """
    Read LLM connection parameters from the SystemConfig table.
    Falls back to environment-based settings if DB rows are missing.
    """
    result = await db.execute(
        select(SystemConfig).where(
            SystemConfig.key.in_([
                "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
                "EMBEDDING_MODEL", "ROUTER_KNN_THRESHOLD",
            ])
        )
    )
    configs = {row.key: row.value for row in result.scalars().all()}

    return {
        "base_url": configs.get("LLM_BASE_URL", settings.LLM_BASE_URL),
        "api_key": configs.get("LLM_API_KEY", settings.LLM_API_KEY),
        "model": configs.get("LLM_MODEL", settings.LLM_MODEL),
        "embedding_model": configs.get("EMBEDDING_MODEL", "bge-m3"),
        "router_knn_threshold": configs.get("ROUTER_KNN_THRESHOLD", "0.35"),
    }


# ---------------------------------------------------------------------------
# Helper: fetch active rule texts per tier (for the agent synthesize node)
# ---------------------------------------------------------------------------

async def _fetch_rule_texts(
    db: AsyncSession,
    class_id,
    lab_id,
    user_id,
) -> dict[str, str | None]:
    """Return active Class/Lab/Student rule texts (or None) for prompt assembly."""
    out: dict[str, str | None] = {"class_rules": None, "lab_rules": None, "student_rules": None}
    targets = [("class_rules", RuleLevel.class_, class_id),
               ("lab_rules", RuleLevel.lab, lab_id),
               ("student_rules", RuleLevel.student, user_id)]
    for key, level, target_id in targets:
        if target_id is None:
            continue
        result = await db.execute(
            select(Rule).where(
                Rule.level == level,
                Rule.target_id == target_id,
                Rule.is_active.is_(True),
            )
        )
        rule = result.scalar_one_or_none()
        if rule:
            out[key] = rule.rules_text
    return out


# ---------------------------------------------------------------------------
# Helper: query-embedding function for the agent's RAG node
# ---------------------------------------------------------------------------

def _make_embed_fn(llm_config: dict[str, str]):
    """Build an async embedder hitting the configured embedding model."""
    async def embed(texts: list[str]) -> list[list[float]]:
        client = AsyncOpenAI(api_key=llm_config["api_key"], base_url=llm_config["base_url"])
        resp = await client.embeddings.create(
            model=llm_config["embedding_model"], input=texts
        )
        return [item.embedding for item in resp.data]
    return embed


# ---------------------------------------------------------------------------
# Helper: Build 3-tier system prompt
# ---------------------------------------------------------------------------

async def _build_system_prompt(
    db: AsyncSession,
    class_id: str | None,
    lab_id: str | None,
    user_id: str,
) -> str:
    """
    Assemble the Master System Prompt from three rule tiers:
    Class → Lab → Student (concatenated sequentially).
    """
    base_prompt = "You are a helpful AI assistant for an educational platform."
    rule_parts: list[str] = []

    # Collect target IDs and their levels
    targets = []
    if class_id:
        targets.append((RuleLevel.class_, class_id))
    if lab_id:
        targets.append((RuleLevel.lab, lab_id))
    targets.append((RuleLevel.student, user_id))

    for level, target_id in targets:
        result = await db.execute(
            select(Rule).where(
                Rule.level == level,
                Rule.target_id == target_id,
                Rule.is_active.is_(True),
            )
        )
        rule = result.scalar_one_or_none()
        if rule:
            label = level.value.upper()
            rule_parts.append(f"[{label} RULES]\n{rule.rules_text}")

    if rule_parts:
        return base_prompt + "\n\n" + "\n\n".join(rule_parts)
    return base_prompt


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
):
    """
    Background task to save messages and upsert usage stats.
    Uses its own DB session since the request session is already closed.
    """
    total_tokens = prompt_tokens + completion_tokens

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
            )
            db.add_all([user_msg, llm_msg])

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
                tokens_used=total_tokens,
                request_count=1,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id", "date"],
                set_={
                    "tokens_used": UsageStat.tokens_used + total_tokens,
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

    if lab_id:
        lab_result = await db.execute(select(Lab).where(Lab.id == lab_id))
        lab = lab_result.scalar_one_or_none()
        if lab:
            class_id = lab.class_id

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

    rule_texts = await _fetch_rule_texts(db, class_id, lab_id, current_user.id)

    agent = build_agent(
        db,
        embed_fn=_make_embed_fn(llm_config),
        router_threshold=float(llm_config["router_knn_threshold"]),
        embedding_model=llm_config["embedding_model"],
    )
    agent_result = await agent.ainvoke({
        "message": body.message,
        "class_id": class_id,
        "lab_id": lab_id,
        "user_id": current_user.id,
        "history": history,
        **rule_texts,
    })
    messages_payload = agent_result["messages_payload"]

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
            # Disable "thinking/reasoning" mode for reasoning-capable models
            # (e.g. Qwen3): otherwise the model emits all tokens in a non-standard
            # `reasoning` field with an empty `content`, so nothing streams to the
            # client. Ollama's OpenAI-compatible endpoint honors this; plain models
            # simply ignore it.
            extra_body={"reasoning_effort": "none"},
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
        try:
            async for chunk in stream:
                if await request.is_disconnected():
                    break

                # Check for token usage
                if chunk.usage:
                    stream_results["prompt_tokens"] = chunk.usage.prompt_tokens
                    stream_results["completion_tokens"] = chunk.usage.completion_tokens

                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        stream_results["content"] += delta.content
                        yield f"data: {delta.content}\n\n"
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
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )
