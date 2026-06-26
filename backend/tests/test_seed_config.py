"""
test_seed_config.py — v7.1 SystemConfig default keys.

The seed must provision the new config-driven knobs so a fresh install has working
defaults: a reranker endpoint/model, the bounded-loop retry budget, and the router
kNN confidence threshold. All model wiring reads these from SystemConfigs at runtime.
"""

from backend.seed import DEFAULT_SYSTEM_CONFIGS


def test_seed_includes_v71_config_keys():
    keys = {c["key"] for c in DEFAULT_SYSTEM_CONFIGS}
    # v7 keys still present
    assert {"LLM_BASE_URL", "LLM_MODEL", "EMBEDDING_MODEL"} <= keys
    # v7.1 additions
    assert {"RERANK_URL", "RERANK_MODEL", "RAG_MAX_RETRIES", "ROUTER_KNN_THRESHOLD"} <= keys


def test_rag_max_retries_defaults_to_one():
    by_key = {c["key"]: c["value"] for c in DEFAULT_SYSTEM_CONFIGS}
    # Bounded loop default is 1 round (admin-configurable).
    assert by_key["RAG_MAX_RETRIES"] == "1"


def test_seed_includes_v72_model_routing_keys():
    keys = {c["key"] for c in DEFAULT_SYSTEM_CONFIGS}
    assert {
        "EMBEDDING_URL", "EMBEDDING_API_KEY", "RERANK_API_KEY",
        "INGEST_MODEL", "INGEST_BASE_URL", "INGEST_API_KEY",
        "ROUTER_MODEL", "ROUTER_BASE_URL", "ROUTER_API_KEY",
        "TOKEN_ALPHA", "TOKEN_BETA",
    } <= keys


def test_v72_split_endpoints_default_empty_for_fallback():
    by_key = {c["key"]: c["value"] for c in DEFAULT_SYSTEM_CONFIGS}
    # Empty = inherit the main LLM, so a fresh install behaves as a single engine.
    for k in ("EMBEDDING_URL", "INGEST_MODEL", "INGEST_BASE_URL", "ROUTER_MODEL"):
        assert by_key[k] == ""


def test_token_weights_default_prefill_cheaper():
    by_key = {c["key"]: c["value"] for c in DEFAULT_SYSTEM_CONFIGS}
    assert by_key["TOKEN_ALPHA"] == "0.2"
    assert by_key["TOKEN_BETA"] == "1.0"
