"""Layout reader contracts: source coverage, bounded inspection, native fidelity."""
import pytest

from backend.worker.page_reader import (
    NativeLine, NativePage, PageReadError, read_page, extract_native_pages,
    chunk_read_pages,
)


def page():
    return NativePage(1, 600, 400, (
        NativeLine('p1l0', (10, 10, 160, 30), 'Allocation'),
        NativeLine('p1l1', (10, 40, 280, 60), 'if (tab == NULL) {'),
        NativeLine('p1l2', (10, 60, 280, 80), '  fprintf(stderr , "plus␣de␣memoire\\n");'),
        NativeLine('p1l3', (10, 80, 280, 100), '}'),
        NativeLine('p1l4', (310, 40, 580, 60), 'Le pointeur doit être vérifié.'),
    ))


def finish():
    return {'action': 'finish', 'groups': [
        {'kind': 'heading', 'ids': ['p1l0']},
        {'kind': 'code', 'ids': ['p1l1', 'p1l2', 'p1l3']},
        {'kind': 'text', 'ids': ['p1l4']},
    ], 'ids': [], 'unreadable': []}


async def render(ids):
    return b'preview' if ids is None else b'crop'


@pytest.mark.asyncio
async def test_groups_keep_exact_native_code_and_separate_prose():
    async def decide(messages):
        return finish()
    result = await read_page(page(), decide=decide, render=render)
    chunks = chunk_read_pages([result])
    code = chunks[1].content
    assert code == '\n'.join(line.text for line in page().lines[1:4])
    assert 'pointeur' not in code
    assert chunks[1].section == 'Allocation'
    assert chunks[1].page_no == 1


@pytest.mark.asyncio
async def test_omission_is_fed_back_before_accepting():
    calls = []
    async def decide(messages):
        calls.append(list(messages))
        result = finish()
        if len(calls) == 1:
            result['groups'].pop()
        return result
    result = await read_page(page(), decide=decide, render=render)
    assert result.calls == 2
    assert 'p1l4' in str(calls[1][-1])
    assert result.groups[-1].text == page().lines[-1].text


@pytest.mark.asyncio
async def test_never_accept_silent_omissions_when_budget_exhausted():
    calls = 0
    async def decide(messages):
        nonlocal calls
        calls += 1
        result = finish()
        result['groups'].pop()
        return result
    with pytest.raises(PageReadError, match='p1l4'):
        await read_page(page(), decide=decide, render=render, max_calls=3)
    assert calls == 3


@pytest.mark.asyncio
async def test_model_can_request_crop_and_then_finish():
    seen = []
    async def capture(ids):
        seen.append(ids)
        return b'png'
    async def decide(messages):
        if len(seen) == 1:
            return {'action': 'inspect', 'ids': ['p1l1', 'p1l2'], 'groups': [], 'unreadable': []}
        return finish()
    result = await read_page(page(), decide=decide, render=capture)
    assert seen == [None, ['p1l1', 'p1l2']]
    assert result.calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('problem', ['duplicate', 'unknown', 'rewrite', 'unreadable', 'empty'])
async def test_reject_bad_results(problem):
    async def decide(messages):
        result = finish()
        if problem == 'duplicate':
            result['groups'][1]['ids'].append('p1l0')
        elif problem == 'unknown':
            result['groups'][1]['ids'].append('invented')
        elif problem == 'rewrite':
            result['groups'][1]['text'] = 'corrected code'
        elif problem == 'unreadable':
            result['unreadable'] = ['bottom clipped']
        else:
            result['groups'] = []
        return result
    with pytest.raises(PageReadError):
        await read_page(page(), decide=decide, render=render, max_calls=1)


@pytest.mark.asyncio
async def test_unknown_crop_id_is_never_rendered():
    seen = []
    async def capture(ids):
        seen.append(ids)
        return b'png'
    async def decide(messages):
        return {'action': 'inspect', 'ids': ['other-page'], 'groups': [], 'unreadable': []}
    with pytest.raises(PageReadError):
        await read_page(page(), decide=decide, render=capture, max_calls=2)
    assert seen == [None]


def test_real_pdf_extraction_preserves_columns_and_stable_ids(tmp_path):
    import pymupdf
    path = tmp_path / 'columns.pdf'
    with pymupdf.open() as pdf:
        p = pdf.new_page(width=600, height=400)
        p.insert_text((20, 40), 'left_code();')
        p.insert_text((320, 40), 'right explanation')
        pdf.save(path)
    pages = extract_native_pages(str(path))
    assert pages == extract_native_pages(str(path))
    assert [line.text for line in pages[0].lines] == ['left_code();', 'right explanation']
    assert pages[0].lines[0].bbox[2] < pages[0].lines[1].bbox[0]


@pytest.mark.asyncio
async def test_image_only_page_is_not_accepted_as_complete():
    async def decide(messages):
        return {'action': 'finish', 'ids': [], 'groups': [], 'unreadable': []}
    with pytest.raises(PageReadError):
        await read_page(NativePage(1, 600, 400, ()), decide=decide, render=render)


@pytest.mark.asyncio
async def test_ingest_uses_layout_before_old_gate_and_stores_provenance(db_session, tmp_path):
    from backend.tests.test_ingest import _seed_document, _chunks_of, fake_embed
    from backend.worker.ingest import ingest_document
    doc = await _seed_document(db_session, tmp_path)
    doc.storage_path += '.pdf'
    await db_session.commit()
    async def decide(messages):
        return finish()
    result = await read_page(page(), decide=decide, render=render)
    async def reader(path):
        return [result], {'version': 'test', 'pages': [{'page_no': 1}]}
    def forbidden_parse(path):
        raise AssertionError('old parse/gate must not run on layout path')
    async def forbidden_repair(*args):
        raise AssertionError('old repair must not rewrite grouped native code')
    await ingest_document(db_session, doc.id, embed_fn=fake_embed,
                          parse_fn=forbidden_parse, repair_fn=forbidden_repair,
                          page_reader_fn=reader)
    chunks = sorted(await _chunks_of(db_session, doc), key=lambda c: c.chunk_index)
    assert chunks[1].content == '\n'.join(l.text for l in page().lines[1:4])
    assert doc.ingest_report['page_reader']['version'] == 'test'
    assert doc.page_count == 1


@pytest.mark.asyncio
async def test_layout_failure_keeps_previous_teacher_edit(db_session, tmp_path):
    from backend.tests.test_ingest import _seed_document, _chunks_of, fake_embed
    from backend.worker.ingest import ingest_document
    from backend.app.models import DocumentStatus
    doc = await _seed_document(db_session, tmp_path)
    await ingest_document(db_session, doc.id, embed_fn=fake_embed)
    old = (await _chunks_of(db_session, doc))[0]
    old.content = 'Teacher correction'
    old.edited_by_teacher = True
    doc.storage_path += '.pdf'
    await db_session.commit()
    async def reader(path):
        raise PageReadError('Page 47 missing realloc')
    await ingest_document(db_session, doc.id, embed_fn=fake_embed, page_reader_fn=reader)
    assert doc.status == DocumentStatus.needs_review
    assert '47' in doc.error_message
    assert (await _chunks_of(db_session, doc))[0].content == 'Teacher correction'


@pytest.mark.asyncio
async def test_non_pdf_never_calls_layout_reader(db_session, tmp_path):
    from backend.tests.test_ingest import _seed_document, _chunks_of, fake_embed
    from backend.worker.ingest import ingest_document
    doc = await _seed_document(db_session, tmp_path)
    async def reader(path):
        raise AssertionError('not a PDF')
    await ingest_document(db_session, doc.id, embed_fn=fake_embed, page_reader_fn=reader)
    assert await _chunks_of(db_session, doc)


def test_worker_builder_reuses_main_model_for_layout(monkeypatch):
    from backend.worker.main import _build_worker_fns
    import backend.worker.page_reader as module
    captured = []
    def factory(client, model):
        captured.append((str(client.base_url), model))
        return 'reader'
    monkeypatch.setattr(module, 'make_page_reader', factory)
    cfg = dict(base_url='http://ingest/v1', api_key='local', model='text-only',
               embedding_base_url='http://embed/v1', embedding_api_key='local',
               embedding_model='embed', page_reader_enabled=True,
               main_base_url='http://main/v1', main_api_key='local', main_model='multimodal')
    fns = _build_worker_fns(cfg)
    assert fns.page_reader_fn == 'reader'
    assert captured == [('http://main/v1/', 'multimodal')]
    cfg['page_reader_enabled'] = False
    assert _build_worker_fns(cfg).page_reader_fn is None


@pytest.mark.asyncio
async def test_pdf_fragments_on_same_baseline_do_not_break_code_comments():
    source = NativePage(1, 600, 400, (
        NativeLine('a', (10, 10, 70, 20), 'free(tab);'),
        NativeLine('b', (90, 10, 200, 20), '// tab est encore'),
        NativeLine('c', (210, 10, 250, 20), 'valide'),
        NativeLine('d', (10, 30, 80, 40), 'return 1;'),
    ))
    async def decide(messages):
        return {'action': 'finish', 'ids': [], 'unreadable': [], 'groups': [
            {'kind': 'code', 'ids': ['a', 'b', 'c', 'd']},
        ]}
    result = await read_page(source, decide=decide, render=render)
    code = chunk_read_pages([result])[0].content
    assert code == 'free(tab); // tab est encore valide\nreturn 1;'


@pytest.mark.asyncio
async def test_consecutive_code_groups_form_one_searchable_example():
    async def decide(messages):
        result = finish()
        result['groups'][1:2] = [
            {'kind': 'code', 'ids': ['p1l1']},
            {'kind': 'code', 'ids': ['p1l2', 'p1l3']},
        ]
        return result
    result = await read_page(page(), decide=decide, render=render)
    chunks = chunk_read_pages([result])
    assert len(chunks) == 3
    assert 'if (tab' in chunks[1].content and 'fprintf' in chunks[1].content


@pytest.mark.asyncio
async def test_content_outside_page_requires_review_without_trusting_model():
    source = NativePage(1, 600, 400, (NativeLine('a', (10, 395, 100, 415), 'clipped'),))
    async def decide(messages):
        return {'action': 'finish', 'ids': [], 'unreadable': [],
                'groups': [{'kind': 'text', 'ids': ['a']}]}
    with pytest.raises(PageReadError, match='outside'):
        await read_page(source, decide=decide, render=render)


@pytest.mark.asyncio
async def test_native_code_indentation_recovered_from_coordinates():
    source = NativePage(1, 600, 400, (
        NativeLine('a', (10, 10, 60, 20), 'if ready:', char_width=5),
        NativeLine('b', (30, 30, 90, 40), 'run()', char_width=5),
    ))
    async def decide(messages):
        return {'action': 'finish', 'ids': [], 'unreadable': [],
                'groups': [{'kind': 'code', 'ids': ['a', 'b']}]}
    result = await read_page(source, decide=decide, render=render)
    assert result.groups[0].text == 'if ready:\n    run()'


@pytest.mark.asyncio
async def test_production_adapter_rejects_truncation_and_records_chunk_sources(tmp_path):
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import pymupdf
    from backend.worker.page_reader import make_page_reader
    path = tmp_path / 'source.pdf'
    with pymupdf.open() as pdf:
        p = pdf.new_page()
        p.insert_text((20, 40), 'original_text();')
        pdf.save(path)
    body = {'next_step': {'action':'finish','decorations':[],'unreadable':[],
                         'groups':[{'kind':'code','ids':['p1l0'],'region_ids':[]}]}}
    def response(reason):
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason=reason, message=SimpleNamespace(content='reasoning</think>' + json.dumps(body)))])
    create = AsyncMock(return_value=response('length'))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    failed, audit = await make_page_reader(client, 'multimodal')(str(path))
    assert failed == []
    assert 'finish normally' in audit['page_results'][0]['error']
    assert create.await_count == 6
    create.return_value = response('stop')
    pages, report = await make_page_reader(client, 'multimodal')(str(path))
    assert report['chunks'][0] == {
        'chunk_index': 0, 'page_no': 1, 'kind': 'code',
        'ids': ['p1l0'], 'start': 0, 'end': len('original_text();'),
    }
    assert 'reasoning' not in str(report)
    assert pages[0].groups[0].text == 'original_text();'


@pytest.mark.asyncio
async def test_separate_side_by_side_code_examples_are_not_merged():
    source = NativePage(1, 600, 400, (
        NativeLine('a', (10, 10, 180, 30), 'left_example();'),
        NativeLine('b', (320, 10, 520, 30), 'right_example();'),
    ))
    async def decide(messages):
        return {'action': 'finish', 'ids': [], 'unreadable': [], 'groups': [
            {'kind': 'code', 'ids': ['a']}, {'kind': 'code', 'ids': ['b']},
        ]}
    result = await read_page(source, decide=decide, render=render)
    assert [c.content for c in chunk_read_pages([result])] == ['left_example();', 'right_example();']


@pytest.mark.asyncio
async def test_unreadable_content_cannot_disappear_on_a_blind_retry():
    calls = 0
    async def decide(messages):
        nonlocal calls
        calls += 1
        result = finish()
        if calls == 1:
            result['unreadable'] = ['diagram has content outside native text']
        return result
    with pytest.raises(PageReadError, match='diagram'):
        await read_page(page(), decide=decide, render=render)
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('unreadable', [False, True])
async def test_missing_centered_footer_number_is_preserved_by_program(unreadable):
    source = NativePage(1, 600, 400, (
        NativeLine('a', (10, 40, 200, 60), 'Actual body text'),
        NativeLine('footer', (297, 385, 303, 391), '16'),
    ))
    calls = 0
    async def decide(messages):
        nonlocal calls
        calls += 1
        return {'action': 'finish', 'ids': [],
                'unreadable': ['footer'] if unreadable else [],
                'groups': [{'kind': 'text', 'ids': ['a']}]}
    result = await read_page(source, decide=decide, render=render)
    assert calls == 1
    assert result.groups[-1].text == '16'
    assert result.groups[-1].kind == 'navigation'


def test_render_view_converts_only_known_page_errors(tmp_path, monkeypatch):
    import pymupdf
    from backend.worker.page_reader import PageRenderError, render_view
    path=tmp_path/'one.pdf'
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((20,40),'native text')
        pdf.save(path)
    native=extract_native_pages(str(path))[0]
    def bad_page(*args,**kwargs):raise pymupdf.mupdf.FzErrorFormat('broken content stream')
    monkeypatch.setattr(pymupdf.Page,'get_pixmap',bad_page)
    with pytest.raises(PageRenderError,match='broken content stream'):
        render_view(str(path),native,None)
    def bug(*args,**kwargs):raise RuntimeError('programming error')
    monkeypatch.setattr(pymupdf.Page,'get_pixmap',bug)
    with pytest.raises(RuntimeError) as caught:
        render_view(str(path),native,None)
    assert not isinstance(caught.value,PageReadError)
    monkeypatch.undo()
    with pytest.raises(PageReadError) as caught:
        render_view(str(path),native,['unknown'])
    assert not isinstance(caught.value,PageRenderError)
    with pytest.raises(Exception) as caught:
        render_view(str(tmp_path/'missing.pdf'),native,None)
    assert not isinstance(caught.value,PageReadError)


def test_lines_starting_at_almost_the_same_x_get_the_same_indentation():
    from backend.worker.page_reader import _native_group_text
    # Geometry of main-3.pdf page 54: a heading slightly left of the code, and two
    # inner lines 0.06 pt apart that rounded to different indentation before.
    rows = [('Correction', 28.35, 5.786), ('if (taille == capacite) {', 29.04, 6.115),
            ('if (temp == NULL) {', 54.14, 6.005), ('free(arr);', 79.38, 6.123),
            ('return 1;', 79.44, 5.978), ('}', 54.14, 6.0), ('arr[taille] = valeur;', 29.04, 6.05)]
    lines = [NativeLine(f'l{i}', (x, 10 + 12 * i, x + 60, 20 + 12 * i), text, width)
             for i, (text, x, width) in enumerate(rows)]
    assert _native_group_text(lines, 'code').split('\n') == [
        'Correction', 'if (taille == capacite) {', '    if (temp == NULL) {', '        free(arr);',
        '        return 1;', '    }', 'arr[taille] = valeur;']
