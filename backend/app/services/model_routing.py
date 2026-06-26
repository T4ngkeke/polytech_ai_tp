"""
model_routing.py — [v7.2] resolve the dynamic ``system_configs`` key/value map into
a structured model-routing config.

v7.2 splits the single LLM into a routing table: generation, embedding, rerank,
ingestion (off-peak worker) and router (live exercise-number fallback) each have
their own optional endpoint/key/model. The rule is **opt-in fallback**: an empty
endpoint/key/model falls back to the main LLM, so existing single-engine setups
behave exactly as before, while an admin can point ingestion/router at a cheap
model without sharing the chat model's RPM pool.

Pure function over a plain mapping — no DB, no HTTP — so it is trivially testable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from backend.app.config import settings

# Defaults that mirror seed.py, used only when a key is entirely absent.
_DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
_DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
_DEFAULT_TOKEN_ALPHA = 0.2
_DEFAULT_TOKEN_BETA = 1.0


@dataclass(frozen=True)
class Endpoint:
    """An OpenAI-compatible endpoint: where to call and which model to ask for."""

    base_url: str
    api_key: str
    model: str


@dataclass(frozen=True)
class ModelRouting:
    llm: Endpoint
    embedding: Endpoint
    rerank: Endpoint
    ingest: Endpoint
    router: Endpoint
    token_alpha: float
    token_beta: float


def _get(configs: Mapping[str, str], key: str, default: str = "") -> str:
    """Treat empty strings the same as missing — both trigger fallback."""
    value = configs.get(key)
    return value if value else default


def _to_float(raw: str, default: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def resolve_model_routing(configs: Mapping[str, str]) -> ModelRouting:
    """Resolve raw ``system_configs`` values into a :class:`ModelRouting`."""
    main_base = _get(configs, "LLM_BASE_URL", settings.LLM_BASE_URL)
    main_key = _get(configs, "LLM_API_KEY", settings.LLM_API_KEY)
    main_model = _get(configs, "LLM_MODEL", settings.LLM_MODEL)

    llm = Endpoint(base_url=main_base, api_key=main_key, model=main_model)

    embedding = Endpoint(
        base_url=_get(configs, "EMBEDDING_URL", main_base),
        api_key=_get(configs, "EMBEDDING_API_KEY", main_key),
        model=_get(configs, "EMBEDDING_MODEL", _DEFAULT_EMBEDDING_MODEL),
    )

    # Rerank URL has NO fallback: empty means "rerank disabled, degrade to
    # fusion-only ordering". Only the API key falls back to the main LLM key.
    rerank = Endpoint(
        base_url=_get(configs, "RERANK_URL", ""),
        api_key=_get(configs, "RERANK_API_KEY", main_key),
        model=_get(configs, "RERANK_MODEL", _DEFAULT_RERANK_MODEL),
    )

    ingest = Endpoint(
        base_url=_get(configs, "INGEST_BASE_URL", main_base),
        api_key=_get(configs, "INGEST_API_KEY", main_key),
        model=_get(configs, "INGEST_MODEL", main_model),
    )

    router = Endpoint(
        base_url=_get(configs, "ROUTER_BASE_URL", main_base),
        api_key=_get(configs, "ROUTER_API_KEY", main_key),
        model=_get(configs, "ROUTER_MODEL", main_model),
    )

    return ModelRouting(
        llm=llm,
        embedding=embedding,
        rerank=rerank,
        ingest=ingest,
        router=router,
        token_alpha=_to_float(_get(configs, "TOKEN_ALPHA", ""), _DEFAULT_TOKEN_ALPHA),
        token_beta=_to_float(_get(configs, "TOKEN_BETA", ""), _DEFAULT_TOKEN_BETA),
    )
