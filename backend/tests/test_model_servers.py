"""The admin sees, for each model slot, what its server really serves."""
import pytest

from backend.app.services import model_servers as ms
from backend.app.services.model_routing import resolve_model_routing


def test_the_models_address_of_any_endpoint_url():
    assert ms.models_url('http://m4-flashnext-v030:8000/v1') == 'http://m4-flashnext-v030:8000/v1/models'
    assert ms.models_url('http://edullm-reranker:8000/rerank') == 'http://edullm-reranker:8000/v1/models'
    assert ms.models_url('') is None and ms.models_url('not a url') is None


@pytest.mark.asyncio
async def test_a_wrong_model_name_and_a_silent_server_are_shown():
    routing = resolve_model_routing({
        'LLM_BASE_URL': 'http://llm:8000/v1', 'LLM_MODEL': 'flash-next',
        'AGENT_MODEL': 'qwen3.8-flash-next',           # not served: every call would 404
        'EMBEDDING_URL': 'http://emb:8000/v1', 'EMBEDDING_MODEL': 'BAAI/bge-m3',
        'RERANK_URL': 'http://down:8000/rerank', 'VLM_MODEL': ''})
    calls = []

    async def fetch(url, key):
        calls.append(url)
        if url.startswith('http://down'):
            raise ms.httpx.ConnectError('refused')
        if url.startswith('http://llm'):
            return {'data': [{'id': 'flash-next', 'max_model_len': 65536}]}
        return {'data': [{'id': 'BAAI/bge-m3', 'max_model_len': 8192}]}

    rows = {r['slot']: r for r in await ms.server_status(routing, fetch=fetch)}
    assert (rows['llm']['status'], rows['llm']['max_model_len'], rows['llm']['server']) == ('ok', 65536, 'llm:8000')
    assert rows['agent']['status'] == 'model_missing' and rows['agent']['served'] == ['flash-next']
    assert rows['vlm']['status'] == 'off'
    assert rows['embedding']['status'] == 'ok' and rows['rerank']['status'] == 'unreachable'
    # Each server is asked once, whatever the number of slots on it.
    assert sorted(calls) == ['http://down:8000/v1/models', 'http://emb:8000/v1/models', 'http://llm:8000/v1/models']
    assert all('key' not in r and 'url' not in r for r in rows.values())   # no API key in the answer


@pytest.mark.asyncio
async def test_only_an_admin_reads_the_servers(db_session, monkeypatch):
    from backend.app.models import UserRole
    from backend.tests.conftest import make_client, make_user

    async def fetch(url, key):
        return {'data': [{'id': 'flash-next', 'max_model_len': 65536}]}

    monkeypatch.setattr(ms, '_fetch', fetch)
    admin, teacher = make_user(role=UserRole.admin), make_user(role=UserRole.teacher)
    db_session.add_all([admin, teacher])
    await db_session.commit()
    async with await make_client(db_session, teacher) as client:
        assert (await client.get('/api/admin/llm/servers')).status_code == 403
    async with await make_client(db_session, admin) as client:
        resp = await client.get('/api/admin/llm/servers')
    assert resp.status_code == 200, resp.text
    assert [r['slot'] for r in resp.json()['servers']] == ['llm', 'agent', 'vlm', 'embedding', 'rerank']


def _refusal(url):
    request = ms.httpx.Request('GET', url)
    return ms.httpx.HTTPStatusError('401', request=request, response=ms.httpx.Response(401, request=request))


@pytest.mark.asyncio
async def test_a_refused_key_is_not_a_silent_server():
    """The model servers answer 401 without the key (model_servers/edullm_key_check.py);
    the page said "server does not answer" before 2026-11-19."""
    async def fetch(url, key):
        if key != 'good':
            raise _refusal(url)
        return {'data': [{'id': 'flash-next', 'max_model_len': 131072}]}

    assert (await ms.check_server('http://llm:8000/v1', 'flash-next', 'bad', fetch=fetch))['status'] == 'refused'
    ok = await ms.check_server('http://llm:8000/v1', 'flash-next', 'good', fetch=fetch)
    assert (ok['status'], ok['max_model_len']) == ('ok', 131072)
    assert (await ms.check_server('http://llm:8000/v1', 'other', 'good', fetch=fetch))['status'] == 'model_missing'
    assert (await ms.check_server('not a url', 'x', 'good', fetch=fetch))['status'] == 'invalid_url'
    # MinerU: no model name in the settings; a server that answers is ok.
    mineru = await ms.check_server('http://mineru:8000/v1', '', 'good', fetch=fetch)
    assert mineru['status'] == 'ok' and mineru['served'] == ['flash-next']
    routing = resolve_model_routing({'LLM_BASE_URL': 'http://llm:8000/v1', 'LLM_MODEL': 'flash-next',
                                     'LLM_API_KEY': 'bad'})
    rows = {r['slot']: r for r in await ms.server_status(routing, fetch=fetch)}
    assert rows['llm']['status'] == 'refused'


@pytest.mark.asyncio
async def test_only_an_admin_checks_a_server(db_session, monkeypatch):
    from backend.app.models import UserRole
    from backend.tests.conftest import make_client, make_user
    seen = []

    async def fetch(url, key):
        seen.append((url, key))
        return {'data': [{'id': 'flash-next', 'max_model_len': 131072}]}

    monkeypatch.setattr(ms, '_fetch', fetch)
    admin, teacher = make_user(role=UserRole.admin), make_user(role=UserRole.teacher)
    db_session.add_all([admin, teacher])
    await db_session.commit()
    body = {'url': 'http://llm:8000/v1', 'model': 'flash-next', 'api_key': 'k'}
    async with await make_client(db_session, teacher) as client:
        assert (await client.post('/api/admin/llm/servers/check', json=body)).status_code == 403
    async with await make_client(db_session, admin) as client:
        resp = await client.post('/api/admin/llm/servers/check', json=body)
    assert resp.status_code == 200 and resp.json()['status'] == 'ok'
    assert seen == [('http://llm:8000/v1/models', 'k')]


def test_the_reranker_switch_keeps_its_address():
    on = resolve_model_routing({'RERANK_URL': 'http://r:8000/rerank'})
    off = resolve_model_routing({'RERANK_URL': 'http://r:8000/rerank', 'RERANK_ENABLED': 'off'})
    assert on.rerank.base_url == 'http://r:8000/rerank' and off.rerank.base_url == ''
