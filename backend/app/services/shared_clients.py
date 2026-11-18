"""
services/shared_clients.py — one HTTP client per model endpoint, reused.

Why: an AsyncOpenAI client that is dropped without being closed is closed later
by the garbage collector in two steps: its socket first, then an aclose() the
SDK schedules on the running loop. When a new connection gets the same file
descriptor number in between, that late aclose() removes the new connection
from the event loop's selector (plain asyncio; uvloop is not affected). The new
connection then never sees that it is connected and waits for its timeout:
this was the 30 s rerank ConnectTimeout. Reusing one client per endpoint and
closing the clients at shutdown removes the dropped clients.

A client belongs to one event loop (its pooled connections do), so the cache
key includes the running loop. Clients of a closed loop are forgotten: their
late close cannot touch another loop's selector.
"""

from __future__ import annotations

import asyncio

import httpx
from openai import AsyncOpenAI

# The reranker answers in well under a second when it is up. A short connect
# timeout and one retry replace a single 30 s wait on a lost connection.
RERANK_TIMEOUT = httpx.Timeout(30.0, connect=3.0)

_clients: dict[tuple, AsyncOpenAI | httpx.AsyncClient] = {}


def _loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _forget_closed_loops() -> None:
    for key in [key for key in _clients if key[0] is not None and key[0].is_closed()]:
        del _clients[key]


def openai_client(base_url: str, api_key: str, *, max_retries: int | None = None,
                  timeout: float | None = None) -> AsyncOpenAI:
    """The AsyncOpenAI client of an endpoint for the running loop.

    None for max_retries or timeout keeps the SDK default. A caller that closes
    the client (an evaluation script at its end) gets a new one next time.
    Outside a running loop the client is cached under no loop; callers should
    ask for it at call time, inside the loop, as ResearchModel does.
    """
    loop = _loop()
    _forget_closed_loops()
    key = (loop, 'openai', base_url, api_key, max_retries, timeout)
    client = _clients.get(key)
    if client is None or client.is_closed():
        options = {}
        if max_retries is not None:
            options['max_retries'] = max_retries
        if timeout is not None:
            options['timeout'] = timeout
        client = _clients[key] = AsyncOpenAI(base_url=base_url, api_key=api_key, **options)
    return client


def http_client(name: str, timeout: httpx.Timeout) -> httpx.AsyncClient:
    """A plain httpx client for the running loop (the rerank endpoint). The
    name identifies the client; the timeout of its first call is kept."""
    loop = _loop()
    _forget_closed_loops()
    key = (loop, 'httpx', name)
    client = _clients.get(key)
    if client is None or client.is_closed:
        client = _clients[key] = httpx.AsyncClient(timeout=timeout)
    return client


async def close_all() -> None:
    """Close the clients of the running loop (and the loop-less ones)."""
    loop = _loop()
    for key in [key for key in _clients if key[0] is loop or key[0] is None]:
        client = _clients.pop(key)
        try:
            await (client.close() if isinstance(client, AsyncOpenAI) else client.aclose())
        except Exception:  # noqa: BLE001 - shutting down; nothing else to do
            pass


class EndpointClient:
    """For a class with an ``endpoint`` (model_routing.Endpoint): ``client`` is
    the shared OpenAI client of that endpoint for the running loop, unless a
    client was set (tests)."""
    _client: AsyncOpenAI | None = None

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is not None:
            return self._client
        return openai_client(self.endpoint.base_url, self.endpoint.api_key or 'local', max_retries=0)

    @client.setter
    def client(self, client: AsyncOpenAI) -> None:
        self._client = client
