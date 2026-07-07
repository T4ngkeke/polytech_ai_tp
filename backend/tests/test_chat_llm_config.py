"""
test_chat_llm_config.py — [v7.2] chat's live config resolution exposes the
independently-configurable embedding endpoint (and falls back to the main LLM).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models import SystemConfig
from backend.app.routers.chat import _get_llm_config, _make_router_llm_fn

pytestmark = pytest.mark.asyncio


async def test_router_llm_fn_calls_router_model_and_returns_content():
    """[v8.0 regression] the one-call router closes over the ROUTER_MODEL endpoint
    with a json_schema response_format and returns the raw JSON string that
    classify() parses — a wiring guard for the (pragma: no cover) live path."""
    cfg = {"router_api_key": "k", "router_base_url": "u", "router_model": "m"}
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = '{"route": "exercise", "exercise_number": 2}'
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=resp)

    with patch("backend.app.routers.chat.AsyncOpenAI", return_value=client):
        router_llm = _make_router_llm_fn(cfg)
        out = await router_llm([{"role": "user", "content": "route this"}])

    assert out == '{"route": "exercise", "exercise_number": 2}'
    _, kwargs = client.chat.completions.create.call_args
    assert kwargs["model"] == "m"
    assert kwargs["response_format"]["type"] == "json_schema"


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
