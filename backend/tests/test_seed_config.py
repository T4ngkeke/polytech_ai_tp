"""
test_seed_config.py — v7.1 SystemConfig default keys.

The seed must provision the config-driven knobs so a fresh install has working
defaults: a reranker endpoint/model and the model slots. All model wiring reads
these from SystemConfigs at runtime.
"""

from backend.seed import DEFAULT_SYSTEM_CONFIGS


def test_seed_includes_v71_config_keys():
    keys = {c["key"] for c in DEFAULT_SYSTEM_CONFIGS}
    # v7 keys still present
    assert {"LLM_BASE_URL", "LLM_MODEL", "EMBEDDING_MODEL"} <= keys
    # v7.1 additions
    assert {"RERANK_URL", "RERANK_MODEL"} <= keys


def test_removed_chat_and_ingest_keys_are_not_seeded():
    """The router, self-eval, rerank-threshold and ingest-model slots left with the old paths."""
    keys = {c["key"] for c in DEFAULT_SYSTEM_CONFIGS}
    assert not {k for k in keys if k.startswith(("ROUTER_", "INGEST_"))}
    assert not {"RAG_MAX_RETRIES", "RERANK_SCORE_THRESHOLD"} & keys


def test_seed_includes_v72_model_routing_keys():
    keys = {c["key"] for c in DEFAULT_SYSTEM_CONFIGS}
    assert {
        "EMBEDDING_URL", "EMBEDDING_API_KEY", "RERANK_API_KEY",
        "TOKEN_ALPHA", "TOKEN_BETA",
    } <= keys


def test_v72_split_endpoints_default_empty_for_fallback():
    by_key = {c["key"]: c["value"] for c in DEFAULT_SYSTEM_CONFIGS}
    # Empty = inherit the main LLM, so a fresh install behaves as a single engine.
    for k in ("EMBEDDING_URL", "RERANK_API_KEY", "AGENT_MODEL"):
        assert by_key[k] == ""


def test_token_weights_default_prefill_cheaper():
    by_key = {c["key"]: c["value"] for c in DEFAULT_SYSTEM_CONFIGS}
    assert by_key["TOKEN_ALPHA"] == "0.2"
    assert by_key["TOKEN_BETA"] == "1.0"


def test_seed_includes_worker_and_agent_keys():
    keys = {row["key"] for row in DEFAULT_SYSTEM_CONFIGS}
    assert {"AGENT_MODEL", "AGENT_BASE_URL", "AGENT_API_KEY"} <= keys


def test_hint_keys_are_gone():
    """Hint generation was removed; its model slot and sampling budget went with it."""
    keys = {row["key"] for row in DEFAULT_SYSTEM_CONFIGS}
    assert not {k for k in keys if k.startswith("HINT_")}


def test_agent_slot_defaults_empty_for_fallback():
    values = {row["key"]: row["value"] for row in DEFAULT_SYSTEM_CONFIGS}
    assert values["AGENT_MODEL"] == ""
    assert values["AGENT_BASE_URL"] == ""
    assert values["AGENT_API_KEY"] == ""
