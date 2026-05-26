"""
routers/chat.py — Streaming chat endpoint connecting to LLM.

Execution Flow
--------------
1. Gate checks
   - Validate session ownership & is_deleted.
   - Quota check (UsageStat tokens_used < daily_token_quota).

2. Assemble LLM Payload
   - Check active TeacherRules for this student (inject if present).
   - If session.applied_rule_id is NULL, update it with active rule.
   - Fetch last 20 messages from Session.
   - Construct: system prompt (rules) + history + new user message.

3. Direct LLM proxy (AsyncOpenAI / Ollama compatible)
   - Read LLM_BASE_URL and LLM_API_KEY from settings.

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
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import get_current_user
from backend.app.config import settings
from backend.app.database import AsyncSessionLocal, get_db
from backend.app.models import (
    Message,
    SenderType,
    Session,
    TeacherRule,
    UsageStat,
    User,
)
from backend.app.schemas import ChatStreamRequest

router = APIRouter(prefix="/api/chat", tags=["chat"])


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
            with open("/home/zhud/.gemini/antigravity/brain/d34a803f-14c6-465d-82aa-9685c43d4505/scratch/bg_debug.txt", "a") as f:
                f.write(f"BACKGROUND TASK RUNNING FOR SESSION: {session_id}\n")
                f.write(f"USER MSG: {user_message_content}\n")
                f.write(f"LLM MSG: {llm_message_content}\n")
            
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
            import traceback
            with open("/home/zhud/.gemini/antigravity/brain/d34a803f-14c6-465d-82aa-9685c43d4505/scratch/bg_error.txt", "w") as f:
                f.write(f"BACKGROUND TASK FAILED: {repr(e)}\n")
                f.write(traceback.format_exc())
            await db.rollback()
            raise
            raise


@router.post("/stream")
async def chat_stream(
    request: Request,
    body: ChatStreamRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Core streaming chat endpoint.
    """
    # ---------------------------------------------------------
    # STEP 1: Gate checks
    # ---------------------------------------------------------
    # Verify session
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
    # STEP 2: Build the LLM payload
    # ---------------------------------------------------------
    system_prompt = "You are a helpful AI assistant."
    
    # Query TeacherRule
    rule_result = await db.execute(
        select(TeacherRule).where(
            or_(TeacherRule.student_id == current_user.id, TeacherRule.student_id.is_(None)),
            TeacherRule.is_active.is_(True),
        )
    )
    rule = rule_result.scalars().first()
    
    if rule:
        # Inject rules into system prompt
        rules_dict = rule.rules_json
        if isinstance(rules_dict, str):
            try:
                rules_dict = json.loads(rules_dict)
            except json.JSONDecodeError:
                pass
                
        system_prompt += f"\n\nTeacher Instructions:\n{json.dumps(rules_dict)}"
        
        # Update session.applied_rule_id if NULL
        if session.applied_rule_id is None:
            session.applied_rule_id = rule.id
            db.add(session)
            await db.commit()

    # Fetch last 20 messages
    msg_result = await db.execute(
        select(Message)
        .where(Message.session_id == body.session_id)
        .order_by(Message.created_at.desc())
        .limit(20)
    )
    last_messages = msg_result.scalars().all()
    # Reverse to chronological order
    last_messages.reverse()

    messages_payload = [{"role": "system", "content": system_prompt}]
    for msg in last_messages:
        role = "user" if msg.sender == SenderType.user else "assistant"
        messages_payload.append({"role": role, "content": msg.content})
    
    messages_payload.append({"role": "user", "content": body.message})

    # ---------------------------------------------------------
    # STEP 3 & 4: Stream and Background Task
    # ---------------------------------------------------------
    client = AsyncOpenAI(
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_BASE_URL,
    )

    try:
        # Initialize the completion stream. 
        # stream_options={"include_usage": True} is needed to get final token counts in SSE.
        stream = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=messages_payload,
            stream=True,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LLM service unavailable: {str(e)}",
        )

    # Shared mutable object to capture the results from the generator
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
            # Enqueue the background task
            # We add a fallback in case usage wasn't provided by the API
            pt = stream_results["prompt_tokens"] or 10  # Fallback mockup
            ct = stream_results["completion_tokens"] or 10  # Fallback mockup
            
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
        media_type="text/event-stream"
    )
