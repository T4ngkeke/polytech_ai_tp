"""What each configured model server really serves (admin LLM Config).

For every slot of the routing map the admin sees whether the server answers,
whether it serves the model name the slot is set to, and that model's context
length (vLLM gives `max_model_len` in /v1/models). A wrong name made every
model call fail with a 404 on 2026-10-16 and nothing on the page showed it.
"""
from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit

import httpx

from backend.app.services.model_routing import ModelRouting

# Slot of the routing map, and the name the page shows.
SLOTS = (('llm', 'Main model'), ('agent', 'Research agent'), ('vlm', 'Vision (VLM)'),
         ('embedding', 'Embeddings'), ('rerank', 'Reranker'))
TIMEOUT_SECONDS = 3.0


def models_url(base_url: str) -> str | None:
    """The /v1/models address of the server behind an endpoint URL (an OpenAI
    base such as http://host:8000/v1, or a rerank URL such as http://host:8000/rerank)."""
    parts = urlsplit(base_url or '')
    if parts.scheme not in ('http', 'https') or not parts.netloc:
        return None
    return f'{parts.scheme}://{parts.netloc}/v1/models'


def check(model: str, payload: dict) -> dict:
    """The slot's state from the server's model list."""
    served = [m.get('id') for m in payload.get('data', []) if isinstance(m, dict)]
    entry = next((m for m in payload.get('data', []) if isinstance(m, dict) and m.get('id') == model), None)
    return {'status': 'ok' if entry else 'model_missing', 'served': served,
            'max_model_len': entry.get('max_model_len') if entry else None}


async def _fetch(url: str, api_key: str) -> dict:
    headers = {'Authorization': f'Bearer {api_key}'} if api_key else {}
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()


# The served context of a model, cached: one /v1/models call per server and
# CONTEXT_TTL seconds at most (the chat asks for each question).
CONTEXT_TTL = 300.0
_contexts: dict[tuple[str, str], tuple[float, int | None]] = {}


async def context_tokens(base_url: str, model: str, api_key: str = '', fetch=None,
                         clock=time.monotonic) -> int | None:
    """The max_model_len the server gives for model, or None when it does not answer."""
    url = models_url(base_url)
    if url is None or not model:
        return None
    cached = _contexts.get((url, model))
    if cached and clock() - cached[0] < CONTEXT_TTL:
        return cached[1]
    try:
        value = check(model, await (fetch or _fetch)(url, api_key))['max_model_len']
    except (httpx.HTTPError, ValueError):
        value = None
    _contexts[(url, model)] = (clock(), value)
    return value


async def server_status(routing: ModelRouting, fetch=None) -> list[dict]:
    """One row per slot: slot, label, model, server (host:port), status
    (ok / model_missing / unreachable / off), served, max_model_len."""
    fetch = fetch or _fetch
    rows, calls = [], {}
    for slot, label in SLOTS:
        endpoint = getattr(routing, slot)
        url = models_url(endpoint.base_url) if endpoint.model else None
        rows.append({'slot': slot, 'label': label, 'model': endpoint.model,
                     'server': urlsplit(url).netloc if url else '', 'url': url, 'key': endpoint.api_key})
        if url:
            calls.setdefault((url, endpoint.api_key), None)

    async def one(url, key):
        try:
            return await fetch(url, key)
        except (httpx.HTTPError, ValueError):
            return None

    answers = dict(zip(calls, await asyncio.gather(*(one(url, key) for url, key in calls))))
    out = []
    for row in rows:
        url, key = row.pop('url'), row.pop('key')
        if url is None:
            out.append({**row, 'status': 'off', 'served': [], 'max_model_len': None})
        elif answers[(url, key)] is None:
            out.append({**row, 'status': 'unreachable', 'served': [], 'max_model_len': None})
        else:
            out.append({**row, **check(row['model'], answers[(url, key)])})
    return out
