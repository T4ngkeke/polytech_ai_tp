"""
routers/auth.py — Authentication endpoints.

Contract endpoint:
  POST /api/auth/login   — validate credentials, return JWT (user_id only)
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import LoginRequest, TokenResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """
    Validate username/password against DB.
    Return a JWT containing ONLY user_id (no role).
    """
    return {"status": "not implemented", "endpoint": "POST /api/auth/login"}
