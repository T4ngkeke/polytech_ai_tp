"""One client per endpoint and event loop: no client is dropped unclosed."""
import asyncio

from backend.app.agent.model import ResearchModel
from backend.app.services import shared_clients
from backend.app.services.model_clients import make_embed_fn
from backend.app.services.model_routing import Endpoint

CONFIG = {'embedding_base_url': 'http://embed.invalid/v1', 'embedding_api_key': 'k',
          'embedding_model': 'e'}


def test_embed_calls_share_one_client(monkeypatch):
    seen = []

    class Embeddings:
        async def create(self, **kwargs):
            class Item:
                embedding, index = [0.0], 0

            class Reply:
                data = [Item()]
            return Reply()

    real = shared_clients.openai_client

    def spy(*args, **kwargs):
        client = real(*args, **kwargs)
        seen.append(client)
        monkeypatch.setattr(client, 'embeddings', Embeddings(), raising=False)
        return client

    monkeypatch.setattr('backend.app.services.model_clients.openai_client', spy)

    async def run():
        embed = make_embed_fn(CONFIG)
        for _ in range(5):
            await embed(['x'])
        await shared_clients.close_all()

    asyncio.run(run())
    assert len(seen) == 5 and len({id(c) for c in seen}) == 1


def test_one_client_per_loop_and_closed_ones_are_replaced():
    async def get():
        return shared_clients.openai_client('http://m.invalid/v1', 'k', max_retries=0)

    async def same_loop():
        first, second = await get(), await get()
        await first.close()
        third = await get()
        await shared_clients.close_all()
        return first, second, third

    first, second, third = asyncio.run(same_loop())
    assert first is second and third is not first
    other = asyncio.run(get())
    assert other is not first
    # The finished loops are forgotten on the next request: only the last
    # loop's client is left.
    asyncio.run(get())
    assert len(shared_clients._clients) == 1


def test_research_model_asks_for_its_client_inside_the_loop():
    model = ResearchModel(Endpoint('http://m.invalid/v1', '', 'm'))

    async def client():
        return model.client

    assert asyncio.run(client()) is not asyncio.run(client())
    model.client = 'fake'
    assert model.client == 'fake'


async def test_the_rerank_call_sends_the_key(monkeypatch):
    """The model servers refuse a call without the key (model_servers/edullm_key_check.py)."""
    from backend.app.services import model_clients
    sent = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {'results': [{'index': 0, 'relevance_score': 0.9}]}

    class Client:
        async def post(self, url, json, headers):
            sent.append(headers)
            return Response()
    monkeypatch.setattr(model_clients, 'http_client', lambda *a: Client())
    config = {'rerank_url': 'http://r:8000/v1/rerank', 'rerank_model': 'm'}
    assert await model_clients.make_rerank_fn({**config, 'rerank_api_key': 'k'})('q', ['d']) == [0.9]
    await model_clients.make_rerank_fn({**config, 'rerank_api_key': ''})('q', ['d'])
    assert sent == [{'Authorization': 'Bearer k'}, {}]
