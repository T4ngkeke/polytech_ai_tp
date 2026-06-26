"""
test_chat_llm_config.py — [v7.2] chat's live config resolution exposes the
independently-configurable embedding endpoint (and falls back to the main LLM).
"""

import pytest

from backend.app.models import SystemConfig
from backend.app.routers.chat import _get_llm_config

pytestmark = pytest.mark.asyncio


async def _seed(db, pairs):
    for k, v in pairs.items():
        db.add(SystemConfig(key=k, value=v))
    await db.flush()


async def test_embedding_endpoint_falls_back_to_main_llm(db_session):
    await _seed(db_session, {
        "LLM_BASE_URL": "http://main/v1",
        "LLM_API_KEY": "main-key",
        "LLM_MODEL": "big",
        "EMBEDDING_MODEL": "bge-m3",
    })
    cfg = await _get_llm_config(db_session)
    assert cfg["embedding_base_url"] == "http://main/v1"
    assert cfg["embedding_api_key"] == "main-key"


async def test_embedding_endpoint_uses_its_own_when_set(db_session):
    await _seed(db_session, {
        "LLM_BASE_URL": "http://main/v1",
        "LLM_API_KEY": "main-key",
        "LLM_MODEL": "big",
        "EMBEDDING_URL": "http://embed/v1",
        "EMBEDDING_API_KEY": "embed-key",
    })
    cfg = await _get_llm_config(db_session)
    assert cfg["embedding_base_url"] == "http://embed/v1"
    assert cfg["embedding_api_key"] == "embed-key"
