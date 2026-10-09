"""Course tools: handles, compact text, budget and evidence, without a database."""
from uuid import uuid4

import pytest

from backend.app.agent.course_tools import CourseTools
from backend.app.agent.evidence import EvidenceStore
from backend.app.agent.result import ResearchQuotaExceeded, ResearchRunStopped
from backend.app.agent.tools import ResearchScope
from backend.app.services.document_access import DocumentNotFound


class ReaderFixture:
    def __init__(self):
        self.doc, self.rev, self.page, self.block = (uuid4() for _ in range(4))
        self.scope = ResearchScope(*(uuid4() for _ in range(5)), {self.doc: self.rev})
        self.executions = self.access_checks = 0
        self.revoked = False

    async def _revision(self, document_id):
        self.access_checks += 1
        if self.revoked or document_id != self.doc:
            raise DocumentNotFound('not_found')

    async def execute(self, name, arguments):
        self.executions += 1
        await self._revision(self.doc)
        continued = bool(arguments.get('cursor'))
        return {'ok': True, 'truncated': not continued,
                'next_cursor': None if continued else 'opaque-next-range',
                'items': [{'block_id': str(self.block), 'type': 'text',
                    'page': {'page_id': str(self.page), 'preview_page_no': 9},
                    'range': {'start': 15 if continued else 10, 'end': 20 if continued else 15},
                    'block_length': 30, 'content': 'world' if continued else 'hello',
                    'source': {'content_origin': 'pdf_native'},
                    'source_completeness': 'partial', 'issues': ['partial_source']} ]}

    def request(self, **extra):
        return {'document_id': str(self.doc), 'page_id': str(self.page), **extra}


@pytest.mark.asyncio
async def test_search_results_do_not_become_verified_evidence():
    tools = ReaderFixture()
    course = CourseTools(tools)
    result = await course.execute('search', {'query': 'restriction'})
    assert result['ok'] and not len(course.evidence)
    assert 'evidence_ids' not in result


@pytest.mark.asyncio
async def test_partial_read_and_its_continuation_are_separate_evidence():
    tools = ReaderFixture()
    course = CourseTools(tools)
    first = await course.execute('read', tools.request())
    assert first['evidence_ids'] == ['E1'] and first['next_cursor'] == 'opaque-next-range'
    assert not course.evidence.get('E1').returned_complete_block
    continued = await course.execute('read', tools.request(cursor=first['next_cursor']))
    assert continued['evidence_ids'] == ['E2'] and tools.executions == 2


@pytest.mark.asyncio
async def test_revoked_access_returns_no_content_and_no_evidence():
    tools = ReaderFixture()
    course = CourseTools(tools)
    assert (await course.execute('read', tools.request()))['ok']
    tools.revoked = True
    result = await course.execute('read', tools.request())
    assert not result['ok'] and 'items' not in result
    assert len(course.evidence) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('stop', [ResearchQuotaExceeded, ResearchRunStopped])
async def test_quota_or_cancel_stops_the_run_instead_of_failing_one_tool(stop):
    class Stopping(ReaderFixture):
        async def execute(self, name, arguments):
            raise stop('stopped')
    tools = Stopping()
    course = CourseTools(tools)
    with pytest.raises(stop):
        await course.execute('read', tools.request())
    assert course.trace[-1]['error'] == 'run_stopped'


def test_only_read_ranges_become_evidence_and_repeats_reuse_their_id():
    doc, rev, page, block = (uuid4() for _ in range(4))
    store = EvidenceStore({doc: rev})

    def read(start, end):
        return {'ok': True, 'items': [{
            'block_id': str(block), 'type': 'text', 'block_length': 30,
            'page': {'page_id': str(page), 'preview_page_no': 3},
            'range': {'start': start, 'end': end}, 'content': 'x' * (end - start)}]}
    search = {'ok': True, 'items': [{'block_id': str(block), 'snippets': [{'content': 'x'}]}]}
    assert store.add_read(doc, search) == []
    assert store.add_read(doc, read(0, 8)) == ['E1']
    assert store.add_read(doc, read(20, 30)) == ['E2']
    assert store.add_read(doc, read(0, 8)) == []
    assert store.add_read(uuid4(), read(0, 8)) == []  # outside the run's snapshot
    assert len(store) == 2 and not store.get('E1').returned_complete_block


class ViewFixture:
    """Two PDF pages (9, 10) of one document, with realistic reader envelopes."""
    def __init__(self):
        self.doc, self.rev, self.p9, self.p10, self.preview = (uuid4() for _ in range(5))
        self.heading, self.text, self.footer, self.figure = (uuid4() for _ in range(4))
        self.scope = ResearchScope(*(uuid4() for _ in range(5)), {self.doc: self.rev})
        self.calls = []

    async def page_index(self):
        return {str(self.doc): {'filename': 'Algebre.pdf', 'pages': [
            {'page_id': str(self.p9), 'pdf_page_no': 9}, {'page_id': str(self.p10), 'pdf_page_no': 10}]}}

    async def _revision(self, document_id):
        if document_id != self.doc:
            raise DocumentNotFound('not_found')

    async def _check_session(self):
        pass

    def _ref(self, page_id, number):
        return {'page_id': str(page_id), 'order_index': number - 1, 'source_slide_no': number,
                'preview_page_no': number, 'preview_asset_id': str(self.preview)}

    def _block(self, block_id, page_id, number, kind, content, start=0, length=None):
        length = len(content) if length is None else length
        return {'block_id': str(block_id), 'page': self._ref(page_id, number), 'type': kind,
                'content': content, 'range': {'start': start, 'end': start + len(content)},
                'block_length': length, 'returned_complete_block': start == 0 and len(content) == length,
                'source': {'content_origin': 'pdf_text_layer', 'extraction_method': 'mineru_hybrid'},
                'source_completeness': 'complete', 'issues': [], 'index_state': 'searchable',
                'edited_by_teacher': False, 'assets': []}

    async def execute(self, name, arguments):
        self.calls.append((name, arguments))
        if name == 'search':
            return {'ok': True, 'issues': [], 'items': [{
                'document_id': str(self.doc), 'revision_id': str(self.rev), 'page_id': str(self.p9),
                'pdf_page_no': 9, 'block_id': str(self.text), 'type': 'text',
                'source_completeness': 'complete', 'issues': [],
                'snippets': [{'chunk_id': str(uuid4()), 'range': {'start': 0, 'end': 17},
                              'content': 'Produit cartésien'}]}]}
        if name == 'read' and arguments.get('page_id') == str(self.p9) and not arguments.get('cursor'):
            figure = self._block(self.figure, self.p9, 9, 'figure', 'Schéma')
            figure['assets'] = [{'asset_id': str(uuid4()), 'kind': 'figure'}]
            return {'ok': True, 'issues': [], 'truncated': True, 'next_cursor': 'signed-page-cursor',
                    'page': self._ref(self.p9, 9), 'unavailable': [], 'items': [
                        self._block(self.heading, self.p9, 9, 'heading', 'Exemple'),
                        self._block(self.footer, self.p9, 9, 'navigation', '9'),
                        self._block(self.text, self.p9, 9, 'text', 'Produit cartésien', length=40),
                        figure]}
        if name == 'read' and arguments.get('cursor'):
            return {'ok': True, 'issues': [], 'truncated': False, 'next_cursor': None,
                    'page': self._ref(self.p9, 9), 'unavailable': [], 'items': [
                        self._block(self.text, self.p9, 9, 'text', ' : combinaisons', start=17, length=40)]}
        if name == 'read' and arguments.get('block_id') == str(self.text):
            return {'ok': True, 'issues': [], 'truncated': False, 'next_cursor': None, 'items': [
                self._block(self.text, self.p9, 9, 'text', 'Produit cartésien', length=40)]}
        if name == 'read' and arguments.get('page_id') == str(self.p10):
            return {'ok': True, 'issues': [], 'truncated': False, 'next_cursor': None,
                    'page': self._ref(self.p10, 10), 'unavailable': [], 'items': [
                        self._block(uuid4(), self.p10, 10, 'text', 'Vin3 = product(Vin1, Années)')]}
        if name == 'open':
            return {'ok': True, 'issues': [], 'document_id': str(self.doc), 'page_count': 2,
                    'next_cursor': None if arguments.get('cursor') else 'signed-open-cursor',
                    'items': [{**self._ref(self.p9, 9), 'title': None, 'summary': 'Exemple'}]}
        if name == 'view_image':
            return {'ok': True, 'issues': [], 'items': [{
                'document_id': str(self.doc), 'revision_id': str(self.rev), 'page_id': arguments['page_id'],
                'pdf_page_no': 9, 'block_id': None, 'asset_id': arguments['asset_id'],
                'origin': 'live_visual', 'content': 'Deux tables', 'uncertainties': ['couleurs']}]}
        raise AssertionError((name, arguments))


@pytest.mark.asyncio
async def test_view_uses_handles_and_hides_reader_metadata():
    tools = ViewFixture()
    course = CourseTools(tools)
    text, ok = await course.call('search', {'query': 'produit cartésien'})
    assert ok and 'd1.p9.b1 text: Produit cartésien' in text and 'd1 = Algebre.pdf' in text
    assert str(tools.text) not in text and not len(course.evidence)
    text, ok = await course.call('read', {'page': 'd1.p9'})
    assert tools.calls[-1] == ('read', {'document_id': str(tools.doc), 'page_id': str(tools.p9)})
    lines = text.splitlines()
    assert lines[0] == 'd1.p9 · Algebre.pdf · page 1 of 2 · first page · next d1.p10 · page image a1'
    # Each line carries its own evidence ID; the footer block is not shown.
    assert lines[1] == '[E1] d1.p9.b2 heading: Exemple'
    assert lines[2] == '[E3] d1.p9.b1 text (characters 0-17 of 40): Produit cartésien'
    assert lines[3] == '[E4] d1.p9.b3 figure (image a2): Schéma'
    assert 'mineru' not in text and 'index_state' not in text and str(tools.p9) not in text
    assert lines[-1] == 'incomplete: continue with read(cursor="c1")'
    assert course.trace[-1]['result']['item_evidence_ids'] == ['E1', 'E2', 'E3', 'E4']


@pytest.mark.asyncio
async def test_next_page_and_cursor_handles_resolve_without_navigate():
    tools = ViewFixture()
    course = CourseTools(tools)
    await course.call('read', {'page': 'd1.p9'})
    text, ok = await course.call('read', {'page': 'd1.p10'})
    assert ok and text.splitlines()[0].startswith('d1.p10 · Algebre.pdf · page 2 of 2 · previous d1.p9 · last page')
    text, ok = await course.call('read', {'cursor': 'c1'})
    assert tools.calls[-1] == ('read', {'document_id': str(tools.doc), 'page_id': str(tools.p9),
                                        'cursor': 'signed-page-cursor'})
    assert '[E6] d1.p9.b2 text (characters 17-32 of 40):  : combinaisons' in text
    await course.call('open', {'page': 'd1.p9'})
    await course.call('open', {'cursor': 'c2'})
    # An open cursor carries its own position and may not repeat the anchor.
    assert tools.calls[-1] == ('open', {'document_id': str(tools.doc), 'cursor': 'signed-open-cursor'})


@pytest.mark.asyncio
async def test_page_image_handle_supplies_page_for_view_image():
    tools = ViewFixture()
    course = CourseTools(tools)
    await course.call('read', {'page': 'd1.p9'})
    text, ok = await course.call('view_image', {'image': 'a1', 'question': 'Quelles tables ?'})
    assert tools.calls[-1] == ('view_image', {'asset_id': str(tools.preview), 'question': 'Quelles tables ?',
                                              'page_id': str(tools.p9)})
    assert ok and text.startswith('[E5] visual observation of a1 on d1.p9 (written by a vision model')
    assert 'uncertain: couleurs' in text


@pytest.mark.asyncio
async def test_unknown_handle_is_refused_before_dispatch_and_takes_a_slot():
    tools = ViewFixture()
    course = CourseTools(tools)
    text, ok = await course.call('read', {'page': 'd1.p99'})
    assert not ok and text.startswith("error: invalid_arguments (unknown page 'd1.p99'")
    text, ok = await course.call('read', {'block': 'b1', 'page': 'd1.p9'})
    assert not ok and 'give exactly one of page, block or cursor' in text
    text, ok = await course.call('read', {'document_id': str(tools.doc), 'page_id': str(tools.p9)})
    assert not ok and 'invalid_arguments' in text
    assert tools.calls == [] and len(course.trace) == 3
    for _ in range(5):
        await course.call('search', {'query': 'x'})
    text, ok = await course.call('read', {'block': 'b1'})
    assert not ok and text.startswith('error: research_budget_exhausted')


def test_model_visible_schemas_name_no_backend_identifiers():
    from backend.app.agent.course_tools import VIEW_SCHEMAS
    for schema in VIEW_SCHEMAS.values():
        names = set(schema.model_json_schema()['properties'])
        assert not names & {'document_id', 'page_id', 'block_id', 'asset_id', 'top_k', 'budget'}


def test_only_short_navigation_blocks_are_hidden():
    from backend.app.agent.course_tools import ToolView
    doc, page = str(uuid4()), str(uuid4())
    view = ToolView({doc: {'filename': 'SQL.pdf', 'pages': [{'page_id': page, 'pdf_page_no': 21}]}})
    ref = {'page_id': page, 'preview_page_no': 21, 'preview_asset_id': None}
    note = 'Interdit, car cru et region ne sont pas au même niveau de regroupement que max(degre)'
    items = [{'block_id': str(uuid4()), 'page': ref, 'type': 'navigation', 'content': text,
              'range': {'start': 0, 'end': len(text)}, 'block_length': len(text)}
             for text in ('21', 'D!', note)]
    text = view.render('read', {'document_id': doc, 'page_id': page},
                       {'ok': True, 'page': ref, 'items': items, 'item_evidence_ids': ['E1', 'E2', 'E3']})
    assert text.splitlines()[1:] == [f'[E3] d1.p21.b1 navigation: {note}']


@pytest.mark.asyncio
async def test_copied_location_resolves_only_to_the_block_shown_there():
    tools = ViewFixture()
    course = CourseTools(tools, max_tool_calls=20)
    await course.call('search', {'query': 'produit cartésien'})  # shows "d1.p9.b1 text: ..."
    for copied in ('d1.p9.b1', 'd1.p9 b1', 'd1.b1', 'b1'):
        text, ok = await course.call('read', {'block': copied})
        assert ok and tools.calls[-1] == ('read', {'document_id': str(tools.doc), 'block_id': str(tools.text)})
    text, ok = await course.call('read', {'page': 'd1.p9 b1'})
    assert ok and tools.calls[-1] == ('read', {'document_id': str(tools.doc), 'page_id': str(tools.p9)})
    calls = len(tools.calls)
    for bad in ({'block': 'd1.p10.b1'}, {'block': 'd2.b1'}, {'block': 'b1 extra'}, {'page': 'd1.p10 b1'}):
        text, ok = await course.call('read', bad)
        assert not ok and text.startswith('error: invalid_arguments')
    assert len(tools.calls) == calls
