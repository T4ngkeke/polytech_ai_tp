"""
test_model_routing.py — [v7.2] the pure SystemConfigs → model-routing resolver.

The resolver turns the raw key/value map from `system_configs` into a structured
routing config, applying the v7.2 fallback rule: embedding / ingest / agent
endpoints fall back to the main LLM when their own values are empty, so the
cheap-model split is opt-in and the default behaviour is unchanged.
"""

from backend.app.services.model_routing import resolve_model_routing


_BASE = {
    "LLM_BASE_URL": "http://main/v1",
    "LLM_API_KEY": "main-key",
    "LLM_MODEL": "qwen3-120b",
    "EMBEDDING_MODEL": "bge-m3",
}


# --- embedding ------------------------------------------------------------


def test_embedding_falls_back_to_main_llm_endpoint_when_unset():
    routing = resolve_model_routing(_BASE)
    assert routing.embedding.base_url == "http://main/v1"
    assert routing.embedding.api_key == "main-key"
    assert routing.embedding.model == "bge-m3"


def test_embedding_uses_its_own_endpoint_when_set():
    routing = resolve_model_routing(
        {**_BASE, "EMBEDDING_URL": "http://embed/v1", "EMBEDDING_API_KEY": "embed-key"}
    )
    assert routing.embedding.base_url == "http://embed/v1"
    assert routing.embedding.api_key == "embed-key"


# --- ingest (off-peak worker) --------------------------------------------


def test_ingest_falls_back_to_main_llm_when_unset():
    routing = resolve_model_routing(_BASE)
    assert routing.ingest.base_url == "http://main/v1"
    assert routing.ingest.api_key == "main-key"
    assert routing.ingest.model == "qwen3-120b"


def test_ingest_uses_cheap_model_when_set():
    routing = resolve_model_routing(
        {
            **_BASE,
            "INGEST_BASE_URL": "http://local/v1",
            "INGEST_API_KEY": "local-key",
            "INGEST_MODEL": "qwen3-30b",
        }
    )
    assert routing.ingest.base_url == "http://local/v1"
    assert routing.ingest.api_key == "local-key"
    assert routing.ingest.model == "qwen3-30b"


def test_ingest_model_override_alone_keeps_main_endpoint():
    routing = resolve_model_routing({**_BASE, "INGEST_MODEL": "qwen3-30b"})
    assert routing.ingest.model == "qwen3-30b"
    assert routing.ingest.base_url == "http://main/v1"
    assert routing.ingest.api_key == "main-key"


# --- rerank: URL has NO fallback (empty = disabled), key does ------------


def test_rerank_url_stays_empty_when_unset_no_fallback():
    routing = resolve_model_routing(_BASE)
    assert routing.rerank.base_url == ""  # empty = rerank disabled, never main LLM


def test_rerank_api_key_falls_back_to_main_when_unset():
    routing = resolve_model_routing({**_BASE, "RERANK_URL": "http://rerank"})
    assert routing.rerank.base_url == "http://rerank"
    assert routing.rerank.api_key == "main-key"


def test_rerank_api_key_uses_its_own_when_set():
    routing = resolve_model_routing(
        {**_BASE, "RERANK_URL": "http://rerank", "RERANK_API_KEY": "rk"}
    )
    assert routing.rerank.api_key == "rk"


# --- token cost weights ---------------------------------------------------


def test_token_weights_default_when_unset():
    routing = resolve_model_routing(_BASE)
    assert routing.token_alpha == 0.2
    assert routing.token_beta == 1.0


def test_token_weights_parse_admin_overrides():
    routing = resolve_model_routing({**_BASE, "TOKEN_ALPHA": "0.5", "TOKEN_BETA": "0.9"})
    assert routing.token_alpha == 0.5
    assert routing.token_beta == 0.9


# --- agent slot (the reasoning model that drives the retrieval loop) ---------
# Formerly the hint slot. Hint generation is gone; the tier it named — big model,
# quality over latency — is what the agent loop wants, so the slot was reused.


def test_agent_falls_back_to_main_llm_when_unset():
    routing = resolve_model_routing(_BASE)
    assert routing.agent.base_url == "http://main/v1"
    assert routing.agent.api_key == "main-key"
    assert routing.agent.model == "qwen3-120b"


def test_agent_uses_its_own_endpoint_when_set():
    routing = resolve_model_routing({
        **_BASE,
        "AGENT_BASE_URL": "http://big/v1",
        "AGENT_API_KEY": "big-key",
        "AGENT_MODEL": "qwen3-235b",
    })
    assert routing.agent.base_url == "http://big/v1"
    assert routing.agent.api_key == "big-key"
    assert routing.agent.model == "qwen3-235b"


# --- [v7.3] worker budgets ----------------------------------------------------


def test_worker_budget_has_a_default_and_parses():
    routing = resolve_model_routing(_BASE)
    assert routing.ingest_token_budget == 200_000

    routing = resolve_model_routing({**_BASE, "INGEST_TOKEN_BUDGET": "50000"})
    assert routing.ingest_token_budget == 50_000


def test_hint_sampling_budget_is_gone():
    routing = resolve_model_routing(_BASE)
    assert not hasattr(routing, "hint_max_samples")


# --- [v8.1] VLM slot (garbled-formula transcription; opt-in, never falls back) ---


def test_vlm_model_empty_means_off_not_fallback():
    """Vision is opt-in: an empty VLM_MODEL stays empty (feature off) — it must
    NEVER silently fall back to the main chat model. URL/key do inherit."""
    routing = resolve_model_routing(_BASE)
    assert routing.vlm.model == ""
    assert routing.vlm.base_url == "http://main/v1"
    assert routing.vlm.api_key == "main-key"


def test_vlm_uses_its_own_endpoint_when_set():
    routing = resolve_model_routing({
        **_BASE,
        "VLM_BASE_URL": "http://vision/v1",
        "VLM_API_KEY": "v-key",
        "VLM_MODEL": "qwen3.5-122b-a10b",
    })
    assert routing.vlm.base_url == "http://vision/v1"
    assert routing.vlm.api_key == "v-key"
    assert routing.vlm.model == "qwen3.5-122b-a10b"


def test_old_chat_path_settings_are_gone():
    routing = resolve_model_routing({**_BASE, "ROUTER_MODEL": "small", "RERANK_SCORE_THRESHOLD": "0.3"})
    assert not hasattr(routing, "router") and not hasattr(routing, "rerank_score_threshold")
