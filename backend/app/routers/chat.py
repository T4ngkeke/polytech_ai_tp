"""
routers/chat.py — Core SSE streaming chat endpoint.

Contract endpoint:
  POST /api/chat/stream — full logic specified in section 5 of the contract.

Steps (stubs only — NOT implemented yet):
  1. IDOR Security Check
  2. Prompt Controller (poison injection)
  3. Dynamic Proxy to LLM (LLM_BASE_URL from .env)
  4. Resilient Stream Buffer (asyncio.timeout + disconnect guard)
  5. Safe Async Write (finally block / BackgroundTask)
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.schemas import ChatRequest

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/stream")
async def chat_stream(
    request: Request,
    payload: ChatRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """
    Core streaming chat endpoint.

    Full logic (IDOR check, prompt controller, SSE proxy, timeout,
    disconnect guard, background DB write) to be implemented in the
    next phase.
    """

    async def _stub_generator():
        yield "data: not implemented\n\n"

    return StreamingResponse(_stub_generator(), media_type="text/event-stream")
