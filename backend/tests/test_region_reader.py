"""Region protocol: the program owns evidence; uncertain figures retain native text."""
import pytest
from backend.tests.test_visual_harness import page, render
from backend.worker.region_reader import read_page


def layout():
    return {'action':'layout','regions':[{'ids':['a'],'region_ids':['ra']},
                                       {'ids':['b'],'region_ids':['rb']}]}


async def run(actions):
    replies=iter(actions)
    async def decide(messages, **kwargs): return next(replies)
    return await read_page(page(),decide=decide,render=render,max_calls=len(actions))


@pytest.mark.asyncio
async def test_review_keeps_native_text_and_no_draft_in_search():
    result=await run([layout(),{'action':'submit','outcome':'review','reason':'Arrow direction unclear'},
                     {'action':'submit','outcome':'native','reason':'The native text contains all meaningful information'}])
    assert [g.text for g in result.page.groups]==['23','45']
    assert len(result.reviews)==1
    assert result.reviews[0].reason=='Arrow direction unclear'
    assert result.reviews[0].ids==('a',)


@pytest.mark.asyncio
async def test_evidence_is_automatic_and_budget_is_shared():
    result=await run([layout(),{'action':'inspect'},
                     {'action':'submit','outcome':'supplement','kind':'formula','content':'2^3'},
                     {'action':'submit','outcome':'native','reason':'The native text contains all meaningful information'}])
    assert result.page.groups[0].text=='2^3'
    assert result.page.calls==4
    assert not result.reviews


@pytest.mark.asyncio
async def test_exhaustion_retains_all_native_lines():
    result=await run([layout()])
    assert {i for g in result.page.groups for i in g.ids}=={'a','b'}
    assert len(result.reviews)==2


@pytest.mark.asyncio
async def test_unknown_source_does_not_remove_native_content():
    result=await run([{'action':'layout','regions':[{'ids':['invented'],'region_ids':[]}]}])
    assert {i for g in result.page.groups for i in g.ids}=={'a','b'}
    assert result.reviews


@pytest.mark.asyncio
async def test_submit_cannot_expand_the_bound_source():
    result=await run([layout(),{'action':'submit','outcome':'supplement','kind':'formula','content':'fake','ids':['b']}])
    assert all('fake' not in g.text for g in result.page.groups)
    assert len(result.reviews)==2


@pytest.mark.asyncio
async def test_extra_crop_does_not_change_bound_source():
    result=await run([layout(),{'action':'inspect','bbox':[292,2,388,38]},
                     {'action':'submit','outcome':'supplement','kind':'formula','content':'fake'}])
    # The automatic initial crop already showed the correct bound source.
    assert result.page.groups[0].text=='fake'
    assert all(g.ids!=('b',) or g.text=='45' for g in result.page.groups)


@pytest.mark.asyncio
async def test_code_cannot_be_replaced_by_formula():
    from dataclasses import replace
    from backend.worker.page_reader import NativeLine
    p=replace(page(),lines=(NativeLine('a',(10,10,80,30),'return x;'),page().lines[1]))
    actions=iter([layout(),{'action':'inspect'},
                  {'action':'submit','outcome':'supplement','kind':'formula','content':'x'}])
    async def decide(messages,**kwargs):return next(actions)
    result=await read_page(p,decide=decide,render=render,max_calls=3)
    assert result.page.groups[0].text=='return x;'
    assert result.reviews


@pytest.mark.asyncio
async def test_formula_format_correction_reuses_existing_observation():
    result=await run([layout(),
                     {'action':'submit','outcome':'supplement','kind':'formula','content':''},
                     {'action':'submit','outcome':'supplement','kind':'formula','content':'2^3'},
                     {'action':'submit','outcome':'native','reason':'The native text contains all meaningful information'}])
    assert result.page.groups[0].text=='2^3'
    assert sum('a' in o['source_ids'] for o in result.page.observations)==1
    assert not result.reviews


@pytest.mark.asyncio
async def test_unexpected_exception_is_not_disguised_as_review():
    async def broken(*args,**kwargs):raise RuntimeError('programming error')
    with pytest.raises(RuntimeError):
        await read_page(page(),decide=broken,render=render)


@pytest.mark.asyncio
async def test_scan_does_not_call_model():
    from dataclasses import replace
    async def unexpected(*args,**kwargs):raise AssertionError('scan model call')
    result=await read_page(replace(page(),lines=()),decide=unexpected,render=render)
    assert result.reviews and result.page.calls==0


@pytest.mark.asyncio
async def test_visible_page_bounds_violation_never_generates_invalid_review_box():
    from dataclasses import replace
    from backend.worker.page_reader import NativeLine
    p=replace(page(),lines=(NativeLine('a',(800,10,900,30),'off-page'),page().lines[1]))
    replies=iter([layout()])
    async def decide(*args,**kwargs):return next(replies)
    result=await read_page(p,decide=decide,render=render,max_calls=1)
    assert all(0<=r.bbox[0]<r.bbox[2]<=p.width for r in result.reviews)
    assert any('source' in r.reason.lower() or 'outside' in r.reason.lower() for r in result.reviews)


@pytest.mark.asyncio
async def test_adapter_reports_partial_separately_and_retains_source(tmp_path):
    import json
    from types import SimpleNamespace
    from backend.worker.region_reader import make_region_reader
    actions=iter([layout(),{'action':'submit','outcome':'review','reason':'Arrow unclear'},
                  {'action':'submit','outcome':'native','reason':'The native text contains all meaningful information'}])
    async def create(**kwargs):
        assert kwargs['temperature']==0
        assert kwargs['response_format']['type']=='json_schema'
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
            message=SimpleNamespace(content=json.dumps(next(actions))))],usage=None)
    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    path=tmp_path/'source.pdf';path.write_bytes(b'fixture')
    reader=make_region_reader(client,'fake',render_fn=render)
    pages,report=await reader(str(path),prepared_pages=[page()])
    assert report['page_results'][0]['status']=='partial'
    assert len(report['reviews'])==1
    assert len(pages[0].groups)==2
    from backend.worker.page_reader import chunk_read_pages
    assert all(c.source_ref['version']==2 for c in chunk_read_pages(pages))


@pytest.mark.asyncio
async def test_layout_selects_visual_ranges_without_relisting_other_text():
    result=await run([{'action':'layout','regions':[{'ids':['a'],'region_ids':['ra']}]},
                     {'action':'submit','outcome':'review','reason':'Unclear direction'}])
    assert {i for g in result.page.groups for i in g.ids}=={'a','b'}
    assert any(r.region_ids==('rb',) for r in result.reviews)
    assert result.page.calls==2


@pytest.mark.asyncio
async def test_tiny_boundary_rounding_does_not_block_entire_page():
    from dataclasses import replace
    from backend.worker.page_reader import NativeLine
    p=replace(page(),lines=(NativeLine('a',(-.2,10,80,30),'23'),page().lines[1]))
    async def decide(*args,**kwargs):return {'action':'layout','regions':[]}
    result=await read_page(p,decide=decide,render=render,max_calls=1)
    assert result.page.calls==1
    assert not any('outside' in r.reason.lower() for r in result.reviews)


def test_native_extraction_isolates_known_page_error(tmp_path, monkeypatch):
    import pymupdf
    import backend.worker.page_reader as module
    path=tmp_path/'pages.pdf'
    with pymupdf.open() as pdf:
        for _ in range(3):pdf.new_page().insert_text((20,40),'native text')
        pdf.save(path)
    original=module._extract_native_page
    def extract(page,number,**kwargs):
        if number==2:raise module.PageReadError('known corrupt text layer')
        return original(page,number,**kwargs)
    monkeypatch.setattr(module,'_extract_native_page',extract)
    pages=module.extract_native_pages(str(path),isolate_errors=True)
    assert [p.page_no for p in pages]==[1,2,3]
    assert pages[1].extraction_error=='known corrupt text layer'
    assert pages[0].lines and pages[2].lines


@pytest.mark.asyncio
async def test_extraction_failure_is_reported_without_calling_model():
    from dataclasses import replace
    async def unexpected(*args,**kwargs):raise AssertionError('model must not run')
    p=replace(page(),lines=(),extraction_error='known corrupt text layer')
    result=await read_page(p,decide=unexpected,render=render)
    assert 'known corrupt' in result.reviews[0].reason


@pytest.mark.asyncio
async def test_region_receives_automatic_actual_crop_before_submit():
    actions=iter([layout(),{'action':'submit','outcome':'supplement','kind':'formula','content':'2^3'},
                  {'action':'submit','outcome':'native','reason':'The native text contains all meaningful information'}])
    seen=[]
    async def decide(messages,**kwargs):
        seen.append(messages)
        return next(actions)
    result=await read_page(page(),decide=decide,render=render,max_calls=3)
    assert not result.reviews
    assert result.page.groups[0].text=='2^3'
    assert any(isinstance(m.get('content'),list) and any(c.get('type')=='image_url' for c in m['content']) for m in seen[1][3:])


@pytest.mark.asyncio
async def test_decoration_decision_does_not_remove_native_text():
    result=await run([{'action':'layout','regions':[], 'decorations':['ra','rb']}])
    assert not result.reviews
    assert {i for g in result.page.groups for i in g.ids}=={'a','b'}
    assert set(result.page.decorations)=={'ra','rb'}


@pytest.mark.asyncio
async def test_native_pdf_block_keeps_code_together():
    from dataclasses import replace
    from backend.worker.page_reader import NativeLine
    p=replace(page(),lines=(NativeLine('a',(10,10,80,20),'if (ok) {',block_id='block1'),
        NativeLine('b',(20,25,80,35),'return 1;',block_id='block1')))
    async def decide(*args,**kwargs):return {'action':'layout','regions':[], 'decorations':['ra','rb']}
    result=await read_page(p,decide=decide,render=render,max_calls=1)
    assert len(result.page.groups)==1
    assert result.page.groups[0].ids==('a','b')
    assert result.page.groups[0].kind=='code'


@pytest.mark.asyncio
async def test_native_sufficiency_requires_an_explicit_reason():
    result=await run([layout(), {'action':'submit','outcome':'native'}])
    assert result.reviews
    assert 'reason' in result.reviews[0].reason.lower()
