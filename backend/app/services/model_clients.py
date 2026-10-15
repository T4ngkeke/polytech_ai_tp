"""
services/model_clients.py — the model endpoints configured in SystemConfig.

The LLM configuration (main model, agent, VLM, embedding, rerank, limits) and
the embedding and rerank functions. The student chat and the teacher reports
build their clients from here.
"""

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import SystemConfig
from backend.app.services.model_routing import resolve_model_routing
from backend.app.services.research_service import ResearchLimits


async def get_llm_config(db: AsyncSession) -> dict:
    """
    Read LLM connection parameters from the SystemConfig table.

    [v7.2] The model-routing table (embedding / rerank / ingest / router split,
    token weights) is resolved via ``resolve_model_routing``, which applies the
    opt-in fallback to the main LLM. The flat keys (``base_url`` etc.) are kept
    for backward compatibility with existing call sites.
    """
    result = await db.execute(select(SystemConfig))
    configs = {row.key: row.value for row in result.scalars().all()}
    routing = resolve_model_routing(configs)

    return {
        "base_url": routing.llm.base_url,
        "api_key": routing.llm.api_key,
        "model": routing.llm.model,
        # [v7.2] embedding endpoint is now independently configurable.
        "embedding_base_url": routing.embedding.base_url,
        "embedding_api_key": routing.embedding.api_key,
        "embedding_model": routing.embedding.model,
        "rerank_url": routing.rerank.base_url,
        "rerank_api_key": routing.rerank.api_key,
        "rerank_model": routing.rerank.model,
        # [v8.1] estimated-token budget for chat history (OOM protection).
        "context_max_tokens": configs.get("CONTEXT_MAX_TOKENS", "8000"),
        "token_alpha": str(routing.token_alpha),
        "token_beta": str(routing.token_beta),
        "agent_base_url": routing.agent.base_url,
        "agent_api_key": routing.agent.api_key,
        "agent_model": routing.agent.model,
        "vlm_base_url": routing.vlm.base_url,
        "vlm_api_key": routing.vlm.api_key,
        "vlm_model": routing.vlm.model,
        "research_limits": ResearchLimits.from_config(configs),
    }


# ---------------------------------------------------------------------------
# Helper: query-embedding function for the agent's RAG node
# ---------------------------------------------------------------------------

def make_embed_fn(llm_config: dict[str, str]):
    """Build an async embedder hitting the configured embedding model."""
    async def embed(texts: list[str]) -> list[list[float]]:
        client = AsyncOpenAI(
            api_key=llm_config["embedding_api_key"],
            base_url=llm_config["embedding_base_url"],
        )
        # encoding_format="float" is mandatory: the OpenAI SDK otherwise defaults
        # to "base64", which some OpenAI-compatible servers (e.g. Albert) reject
        # with a 500. Float is universally supported.
        resp = await client.embeddings.create(
            model=llm_config["embedding_model"], input=texts, encoding_format="float"
        )
        return [item.embedding for item in resp.data]
    return embed


# Qwen3-Reranker is a chat model asked "yes" or "no" (vLLM serves it as a
# classifier with --hf-overrides, see its model card); the client wraps the
# query and each passage in its prompt. Other rerankers get the plain texts.
QWEN3_RERANK_PREFIX = (
    '<|im_start|>system\nJudge whether the Document meets the requirements based on the Query '
    'and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n'
    '<|im_start|>user\n')
QWEN3_RERANK_SUFFIX = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
RERANK_TASK = 'Given a student question about a course, retrieve the course passages that answer it'


def rerank_inputs(model: str, query: str, documents: list[str]) -> tuple[str, list[str]]:
    """The query and passages as the rerank model expects them."""
    if 'qwen3-reranker' not in (model or '').lower():
        return query, documents
    return (f'{QWEN3_RERANK_PREFIX}<Instruct>: {RERANK_TASK}\n<Query>: {query}\n',
            [f'<Document>: {doc}{QWEN3_RERANK_SUFFIX}' for doc in documents])


def make_rerank_fn(llm_config: dict[str, str]):
    """Build a reranker hitting the configured rerank endpoint, or None if unset.

    Expects an OpenAI-compatible rerank endpoint (TEI / Infinity / vLLM) returning
    `{"results": [{"index": i, "relevance_score": s}, ...]}`. When `RERANK_URL` is
    empty, hybrid retrieval degrades gracefully to fusion-only ordering.
    """
    url = llm_config.get("rerank_url") or ""
    if not url:
        return None

    import httpx

    async def rerank(query: str, documents: list[str]) -> list[float]:  # pragma: no cover
        query, documents = rerank_inputs(llm_config["rerank_model"], query, documents)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json={
                "model": llm_config["rerank_model"],
                "query": query,
                "documents": documents,
            })
            resp.raise_for_status()
            results = resp.json().get("results", [])
        scores = [0.0] * len(documents)
        for item in results:
            scores[item["index"]] = item["relevance_score"]
        return scores
    return rerank
