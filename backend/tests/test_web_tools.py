"""test_web_tools.py — web search (ported from Open WebUI) and its safety checks."""
import asyncio
import json
import socket

import httpx
import pytest

from backend.app.agent import web_tools as wt
from backend.app.agent.course_tools import CourseTools
from backend.app.agent.runner import ResearchRunner, web_citations
from backend.tests.test_course_tools import ViewFixture
from backend.tests.test_runner import Script


def resolving(monkeypatch, *addresses):
    """Make every host name resolve to these addresses."""
    def fake(host, port, *args, **kwargs):
        family = lambda a: socket.AF_INET6 if ':' in a else socket.AF_INET
        return [(family(a), socket.SOCK_STREAM, 6, '', (a, port or 0)) for a in addresses]
    monkeypatch.setattr(socket, 'getaddrinfo', fake)


@pytest.mark.parametrize('url', [
    'file:///etc/passwd', 'ftp://example.com/x', 'gopher://example.com',
    'http://127.0.0.1\\@1.1.1.1/', 'http://exa mple.com/', 'http://example.com/\nx', 'http:///nohost',
])
def test_urls_that_are_refused_before_any_lookup(url):
    with pytest.raises(ValueError):
        wt.validate_url(url)


@pytest.mark.parametrize('address', [
    '127.0.0.1', '10.0.0.5', '192.168.168.199', '172.17.0.2', '169.254.169.254', '100.108.32.29',
    '0.0.0.0', '::1', 'fe80::1', '::ffff:127.0.0.1', '64:ff9b::7f00:1', '2002:7f00:1::',
])
def test_hosts_that_resolve_to_inner_addresses_are_refused(monkeypatch, address):
    resolving(monkeypatch, address)
    with pytest.raises(ValueError):
        wt.validate_url('https://looks-public.example/')


def test_a_public_address_passes(monkeypatch):
    resolving(monkeypatch, '93.184.215.14')
    assert wt.validate_url('https://example.com/page')


def test_one_inner_address_among_public_ones_is_enough_to_refuse(monkeypatch):
    resolving(monkeypatch, '93.184.215.14', '127.0.0.1')
    with pytest.raises(ValueError):
        wt.validate_url('https://example.com/')


def test_compressed_bodies_are_refused():
    wt.assert_identity_encoding(['identity'])
    with pytest.raises(ValueError):
        wt.assert_identity_encoding(['gzip'])


def test_page_text_drops_scripts_and_keeps_lines():
    title, text = wt._page_text('<html><head><title>Récursivité</title><script>evil()</script></head>'
                                '<body><h1>Cas de base</h1><p>Il arrête la récursion.</p></body></html>',
                                'https://example.com')
    assert title == 'Récursivité' and text == 'Récursivité\nCas de base\nIl arrête la récursion.'


def test_searxng_results_best_first():
    def handler(request):
        assert request.url.path == '/search' and request.url.params['format'] == 'json'
        return httpx.Response(200, json={'results': [
            {'url': 'https://b.org', 'title': 'B', 'content': 'b', 'score': 1},
            {'url': 'https://a.org', 'title': 'A', 'content': 'a', 'score': 3},
            {'title': 'no url', 'score': 9},
            {'url': 'http:///go/v1/AXtS', 'title': 'no host', 'score': 8}]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = asyncio.run(wt.search_searxng('http://searxng:8080', 'récursion', 5, client=client))
    assert [r['link'] for r in out] == ['https://a.org', 'https://b.org']


def fake_tools(**kw):
    async def search(url, query, count):
        return [{'title': 'Python docs', 'link': 'https://docs.python.org/3/', 'snippet': 'sys.setrecursionlimit'},
                {'title': 'Real Python', 'link': 'https://realpython.com/', 'snippet': 'recursion'}][:count]

    def fetch(url):
        if 'blocked' in url:
            raise ValueError(wt.INVALID_URL)
        return 'Python docs', 'The limit is 1000 by default.'
    return wt.WebTools('http://searxng:8080', search=search, fetch=fetch, **kw)


def test_results_and_pages_get_stable_ids_and_errors_go_back_to_the_model():
    tools = fake_tools(max_calls=3)
    found = json.loads(asyncio.run(tools.call('search_web', json.dumps({'query': 'limite récursion'}))))
    assert [r['id'] for r in found] == ['W1', 'W2']
    page = json.loads(asyncio.run(tools.call('fetch_url', json.dumps({'url': 'https://docs.python.org/3/'}))))
    assert page['id'] == 'W1' and 'limit is 1000' in page['text']                    # same URL, same ID
    blocked = json.loads(asyncio.run(tools.call('fetch_url', json.dumps({'url': 'http://blocked/'}))))
    assert 'invalid' in blocked['error']
    assert 'At most 3' in json.loads(asyncio.run(tools.call('search_web', '{"query": "x"}')))['error']


def test_only_returned_web_ids_stay_in_the_answer():
    tools = fake_tools()
    asyncio.run(tools.call('search_web', json.dumps({'query': 'x'})))
    text, sources = web_citations('Voir [W1] et [W2, W9], pas [W7].', tools)
    assert text == 'Voir [W1] et [W2], pas .'
    assert [s['id'] for s in sources] == ['W1', 'W2']
    assert web_citations('Sans [W1].', None) == ('Sans [W1].', None)


@pytest.mark.asyncio
async def test_the_loop_offers_web_only_when_given_and_returns_its_sources():
    web = fake_tools()
    model = Script([('search_web', {'query': 'limite de récursion python'})],
                   'Python limite la pile à 1000 appels [W1].')
    result = await ResearchRunner(CourseTools(ViewFixture()), model, web_tools=web).run('Limite ?')
    assert result.web_sources == [{'id': 'W1', 'url': 'https://docs.python.org/3/', 'title': 'Python docs'}]
    assert result.tool_calls == 0 and result.citations == []           # not course evidence
    assert wt.WEB_NOTE in model.seen[0][0]['content']
    plain = Script('Bonjour.')
    result = await ResearchRunner(CourseTools(ViewFixture()), plain).run('Bonjour')
    assert result.web_sources is None and wt.WEB_NOTE not in plain.seen[0][0]['content']


def test_a_dns_answer_that_changes_after_the_check_is_stopped_at_connect(monkeypatch):
    answers = iter(['93.184.215.14', '127.0.0.1', '127.0.0.1', '127.0.0.1'])
    connected = []

    def fake(host, port, *args, **kwargs):
        address = next(answers)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, port or 0))]
    monkeypatch.setattr(socket, 'getaddrinfo', fake)
    monkeypatch.setattr(socket.socket, 'connect', lambda self, sa: connected.append(sa))
    with pytest.raises(Exception) as err:
        wt.fetch_page('http://rebinding.example/')
    assert 'invalid' in str(err.value).lower() and connected == []


@pytest.mark.asyncio
async def test_a_lab_without_course_files_keeps_web_search():
    from backend.app.agent.runner import COURSE_TOOL_NAMES, NO_DOCUMENTS_WEB_TURN

    class EmptyLab(ViewFixture):
        async def page_index(self):
            return {}
    offered = []

    class Recording(Script):
        async def __call__(self, messages, max_output, timeout, *, tools=None, purpose=''):
            offered.append({t['function']['name'] for t in tools or []})
            return await super().__call__(messages, max_output, timeout, tools=tools, purpose=purpose)
    model = Recording([('search_web', {'query': 'dernière version Python'})], 'Python 3.14 [W1].')
    result = await ResearchRunner(CourseTools(EmptyLab()), model, web_tools=fake_tools()).run('Version ?')
    assert 'search_web' in offered[0] and not COURSE_TOOL_NAMES & offered[0]
    assert NO_DOCUMENTS_WEB_TURN in model.seen[0][-1]['content']
    assert [s['id'] for s in result.web_sources] == ['W1'] and result.answer == 'Python 3.14 [W1].'
