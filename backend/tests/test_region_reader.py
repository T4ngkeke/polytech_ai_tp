"""Region protocol: the program owns evidence; uncertain figures retain native text."""
import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError

from backend.tests.test_visual_harness import page, render
from backend.worker.page_reader import NativeLine, NativePage, NativeRegion, PageRenderError, chunk_read_pages
from backend.worker.region_reader import VERSION, make_review, read_page, response_format


def layout():
    return {'action':'layout','regions':[{'ids':['a'],'region_ids':['ra']},
                                       {'ids':['b'],'region_ids':['rb']}]}


def formula(content='2^3'):
    return {'action':'submit','outcome':'supplement','kind':'formula','content':content}


def review(reason='Arrow direction unclear', **extra):
    return {'action':'submit','outcome':'review','reason':reason,**extra}


async def run(actions, p=None, **kwargs):
    replies=iter(actions)
    async def decide(messages, **kwargs): return next(replies)
    return await read_page(p or page(),decide=decide,render=kwargs.pop('render',render),
                           max_calls=kwargs.pop('max_calls',len(actions)))


def searchable(result):
    return '\n'.join(g.text for g in result.page.groups)


def connection_error():
    return APIConnectionError(request=httpx.Request('POST','http://model.test/v1'))


@pytest.mark.asyncio
async def test_review_keeps_native_text_and_no_draft_in_search():
    result=await run([layout(),review(),formula('4^5')])
    assert [g.text for g in result.page.groups]==['23','4^5']
    assert len(result.reviews)==1
    assert result.reviews[0].reason=='Arrow direction unclear'
    assert result.reviews[0].reason_code=='content_review'
    assert result.reviews[0].ids==('a',)


@pytest.mark.asyncio
async def test_evidence_is_automatic_and_budget_is_shared():
    result=await run([layout(),{'action':'inspect'},formula(),formula('4^5')])
    assert result.page.groups[0].text=='2^3'
    assert result.page.calls==4
    assert not result.reviews


@pytest.mark.asyncio
async def test_exhaustion_retains_all_native_lines():
    result=await run([layout()])
    assert {i for g in result.page.groups for i in g.ids}=={'a','b'}
    assert len(result.reviews)==2
    assert {r.reason_code for r in result.reviews}=={'budget_exhausted'}


@pytest.mark.asyncio
async def test_unknown_source_does_not_remove_native_content():
    result=await run([{'action':'layout','regions':[{'ids':['invented'],'region_ids':[]}]}])
    assert {i for g in result.page.groups for i in g.ids}=={'a','b'}
    assert result.reviews
    assert result.reviews[0].reason_code=='protocol_failure'


@pytest.mark.asyncio
async def test_submit_cannot_expand_the_bound_source():
    result=await run([layout(),{**formula('fake'),'ids':['b']}])
    assert all('fake' not in g.text for g in result.page.groups)
    assert len(result.reviews)==2
    # The range that received the bad reply is a protocol failure; the untouched
    # range only ran out of budget.
    assert [r.reason_code for r in result.reviews]==['protocol_failure','budget_exhausted']


@pytest.mark.asyncio
async def test_extra_crop_does_not_change_bound_source():
    result=await run([layout(),{'action':'inspect','bbox':[292,2,388,38]},formula('fake')])
    # The automatic initial crop already showed the correct bound source.
    assert result.page.groups[0].text=='fake'
    assert all(g.ids!=('b',) or g.text=='45' for g in result.page.groups)


@pytest.mark.asyncio
async def test_code_cannot_be_replaced_by_formula():
    p=replace(page(),lines=(NativeLine('a',(10,10,80,30),'return x;'),page().lines[1]))
    result=await run([layout(),{'action':'inspect'},formula('x')],p)
    assert result.page.groups[0].text=='return x;'
    assert result.reviews


@pytest.mark.asyncio
async def test_formula_format_correction_reuses_existing_observation():
    result=await run([layout(),formula(''),formula(),formula('4^5')])
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
    async def unexpected(*args,**kwargs):raise AssertionError('scan model call')
    result=await read_page(replace(page(),lines=()),decide=unexpected,render=render)
    assert result.reviews and result.page.calls==0
    assert result.reviews[0].reason_code=='content_review'


@pytest.mark.asyncio
async def test_visible_page_bounds_violation_never_generates_invalid_review_box():
    p=replace(page(),lines=(NativeLine('a',(800,10,900,30),'off-page'),page().lines[1]))
    result=await run([layout()],p,max_calls=1)
    assert all(0<=r.bbox[0]<r.bbox[2]<=p.width for r in result.reviews)
    assert [r.reason_code for r in result.reviews]==['source_geometry_unverified']
    assert result.failure_kind=='source_geometry_unverified'
    assert {i for g in result.page.groups for i in g.ids}=={'a','b'}


@pytest.mark.asyncio
async def test_adapter_reports_partial_separately_and_retains_source(tmp_path):
    actions=iter([layout(),review('Arrow unclear'),formula('4^5')])
    async def create(**kwargs):
        assert kwargs['temperature']==0
        assert kwargs['response_format']['type']=='json_schema'
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
            message=SimpleNamespace(content=json.dumps(next(actions))))],usage=None)
    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    path=tmp_path/'source.pdf';path.write_bytes(b'fixture')
    from backend.worker.region_reader import make_region_reader
    reader=make_region_reader(client,'fake',render_fn=render)
    pages,report=await reader(str(path),prepared_pages=[page()])
    assert report['version']==VERSION=='region-reader-v1.8'
    assert pages[0].reader_version==VERSION
    assert report['page_results'][0]['status']=='partial'
    assert len(report['reviews'])==1
    assert report['reviews'][0]['reason_code']=='content_review'
    assert len(pages[0].groups)==2
    assert all(c.source_ref['version']==3 for c in chunk_read_pages(pages,file_sha256=report['source_sha256']))
    assert report['reviews'][0]['source_ref']['version']==3


@pytest.mark.asyncio
async def test_layout_selects_visual_ranges_without_relisting_other_text():
    result=await run([{'action':'layout','regions':[{'ids':['a'],'region_ids':['ra']}],'decorations':['rb']},review('Unclear direction')])
    assert {i for g in result.page.groups for i in g.ids}=={'a','b'}
    assert [r.ids for r in result.reviews]==[('a',)]
    assert result.page.calls==2


@pytest.mark.asyncio
async def test_layout_must_account_for_every_candidate_graphic():
    seen=[]
    replies=iter([{'action':'layout','regions':[{'ids':['a'],'region_ids':['ra']}]},
                  {'action':'layout','regions':[{'ids':['a'],'region_ids':['ra']}],'decorations':['rb']},
                  formula()])
    async def decide(messages,**kwargs):
        seen.append(messages)
        return next(replies)
    result=await read_page(page(),decide=decide,render=render,max_calls=6)
    error=json.loads(seen[1][2]['content'])['error']
    assert 'rb' in error and 'decorations' in error
    assert json.loads(seen[1][2]['content'])['phase']=='layout'
    assert not result.reviews
    assert result.page.calls==3 and set(result.page.decorations)=={'rb'}


@pytest.mark.asyncio
async def test_graphic_still_unassessed_when_budget_ends_goes_to_a_teacher():
    incomplete={'action':'layout','regions':[{'ids':['a'],'region_ids':['ra']}]}
    result=await run([incomplete,incomplete])
    # Only the missing graphic is a content question; the unprocessed range ran out of calls.
    assert {(r.region_ids,r.reason_code) for r in result.reviews}=={(('ra',),'budget_exhausted'),(('rb',),'content_review')}
    assert next(r for r in result.reviews if r.region_ids==('rb',)).reason=='Graphic not assessed in layout'
    assert {i for g in result.page.groups for i in g.ids}=={'a','b'}
    assert result.page.calls==2


def test_prompt_and_schema_state_the_figure_description_limit():
    from backend.worker.region_reader import PROMPT
    from backend.worker.visual_harness import FIGURE_DESCRIPTION_MAX
    assert str(FIGURE_DESCRIPTION_MAX) in PROMPT
    assert 'decorations' in PROMPT and 'exactly once' in PROMPT
    actions=response_format('region',page())['json_schema']['schema']['properties']['next_step']['anyOf']
    figure=[a for a in actions if a['properties'].get('kind',{}).get('enum')==['figure']]
    assert len(figure)==1 and str(FIGURE_DESCRIPTION_MAX) in figure[0]['properties']['content']['description']
    # Stated, not enforced by the grammar: a forced cut would end a sentence mid-way.
    assert 'maxLength' not in figure[0]['properties']['content']


@pytest.mark.asyncio
async def test_tiny_boundary_rounding_does_not_block_entire_page():
    p=replace(page(),lines=(NativeLine('a',(-.2,10,80,30),'23'),page().lines[1]))
    result=await run([{'action':'layout','regions':[]}],p,max_calls=1)
    assert result.page.calls==1
    assert not any(r.reason_code=='source_geometry_unverified' for r in result.reviews)


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
    async def unexpected(*args,**kwargs):raise AssertionError('model must not run')
    p=replace(page(),lines=(),extraction_error='known corrupt text layer')
    result=await read_page(p,decide=unexpected,render=render)
    assert 'known corrupt' in result.reviews[0].reason
    assert result.reviews[0].reason_code=='extraction_failure'
    assert result.failure_kind=='extraction_failure'


@pytest.mark.asyncio
async def test_region_receives_automatic_actual_crop_before_submit():
    actions=iter([layout(),formula(),formula('4^5')])
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
    p=replace(page(),lines=(NativeLine('a',(10,10,80,20),'if (ok) {',block_id='block1'),
        NativeLine('b',(20,25,80,35),'return 1;',block_id='block1')))
    result=await run([{'action':'layout','regions':[], 'decorations':['ra','rb']}],p,max_calls=1)
    assert len(result.page.groups)==1
    assert result.page.groups[0].ids==('a','b')
    assert result.page.groups[0].kind=='code'


# --- v1.5: a selected range ends with a supplement or a review, never "native" ---

@pytest.mark.asyncio
async def test_selected_range_cannot_end_as_native_but_unselected_text_is_kept():
    seen=[]
    replies=iter([{'action':'layout','regions':[{'ids':['a'],'region_ids':['ra']}],'decorations':['rb']},
                  {'action':'submit','outcome':'native','reason':'Text is enough'},
                  review('Exponent position unclear')])
    async def decide(messages,**kwargs):
        seen.append(messages)
        return next(replies)
    result=await read_page(page(),decide=decide,render=render,max_calls=3)
    state=json.loads(seen[2][2]['content'])
    assert 'supplement' in state['error'] and 'review' in state['error']
    assert result.page.trace[1]['error']==state['error']
    assert [r.ids for r in result.reviews]==[('a',)]
    assert [g.text for g in result.page.groups]==['23','45']


@pytest.mark.asyncio
async def test_repeated_native_until_budget_is_a_protocol_failure():
    native={'action':'submit','outcome':'native','reason':'Text is enough'}
    result=await run([{'action':'layout','regions':[{'ids':['a'],'region_ids':['ra']}],'decorations':['rb']},native,native])
    assert [r.reason_code for r in result.reviews]==['protocol_failure']
    assert result.page.groups[0].text=='23'


def test_schema_offers_no_native_outcome_and_nullable_candidate():
    schema=json.dumps(response_format('region',page()))
    assert '"native"' not in schema
    actions=response_format('region',page())['json_schema']['schema']['properties']['next_step']['anyOf']
    reviews=[a for a in actions if a['properties'].get('outcome',{}).get('enum')==['review']]
    assert len(reviews)==1
    assert set(reviews[0]['required'])=={'action','outcome','reason','candidate_content'}
    assert reviews[0]['properties']['candidate_content']['type']==['string','null']


def three_kinds_page():
    return NativePage(1,600,400,(NativeLine('f',(10,10,80,30),'box calls box'),
        NativeLine('m',(200,10,280,30),'264-1'),NativeLine('t',(400,10,480,30),'a b 1 2')),
        (NativeRegion('rf',(10,10,80,30),'drawing'),NativeRegion('rm',(200,10,280,30),'drawing'),
         NativeRegion('rt',(400,10,480,30),'drawing')))


async def box_render(ids=None,*,bbox=None):
    from backend.worker.visual_harness import RenderedView
    if bbox: return RenderedView(b'png',tuple(bbox))
    if not ids: return RenderedView(b'png',(0,0,600,400))
    left={'rf':0,'rm':190,'rt':390,'f':0,'m':190,'t':390}[ids[0]]
    return RenderedView(b'png',(left+2,2,left+90,38))


@pytest.mark.asyncio
async def test_one_protocol_for_figure_formula_and_table():
    result=await run([
        {'action':'layout','regions':[{'ids':['f'],'region_ids':['rf']},{'ids':['m'],'region_ids':['rm']},
                                      {'ids':['t'],'region_ids':['rt']}]},
        {'action':'submit','outcome':'supplement','kind':'figure','content':'The left box calls the right box.'},
        formula('2^{64}-1'),
        {'action':'submit','outcome':'supplement','kind':'table','rows':[['a','b'],['1','2']]}],
        three_kinds_page(),render=box_render)
    assert not result.reviews
    assert [g.kind for g in result.page.groups]==['figure','formula','table']
    assert result.page.groups[0].text=='The left box calls the right box.\nbox calls box'
    assert result.page.groups[1].text=='2^{64}-1'
    assert result.page.groups[2].text=='a | b\n1 | 2'


@pytest.mark.asyncio
async def test_review_candidate_is_cleaned_and_never_searchable():
    result=await run([{'action':'layout','regions':[{'ids':['a'],'region_ids':['ra']}],'decorations':['rb']},
                      review('Arrow direction unclear',
                             candidate_content='<think>guessing</think> Object A probably calls B')])
    assert result.reviews[0].candidate_content=='Object A probably calls B'
    assert 'probably' not in searchable(result) and 'guessing' not in searchable(result)
    assert all('probably' not in c.content and 'probably' not in (c.source_text or '')
               and 'probably' not in json.dumps(c.source_ref)
               for c in chunk_read_pages([result.page]))


@pytest.mark.asyncio
async def test_review_with_null_or_blank_candidate_stores_none():
    result=await run([layout(),review(candidate_content=None),review(candidate_content='  ')])
    assert [r.candidate_content for r in result.reviews]==[None,None]


@pytest.mark.asyncio
async def test_overlong_candidate_is_a_format_error_not_a_review():
    result=await run([{'action':'layout','regions':[{'ids':['a'],'region_ids':['ra']}],'decorations':['rb']},
                      review(candidate_content='x'*10001),review(candidate_content='x'*10000)])
    assert len(result.reviews)==1
    assert result.reviews[0].candidate_content=='x'*10000
    assert result.page.trace[1]['error']


def test_make_review_builds_one_record_format():
    p=page()
    record=make_review(p,{'ids':['a'],'region_ids':['ra']},reason='  unclear ',reason_code='render_failure',
                       candidate_content='</think>maybe')
    assert record.page_no==1 and record.ids==('a',) and record.region_ids==('ra',)
    assert record.bbox==(10,10,80,30)
    assert record.reason=='unclear' and record.reason_code=='render_failure'
    assert record.candidate_content=='maybe'
    assert record.source_ref is None
    with pytest.raises(ValueError):
        make_review(p,{'ids':['a'],'region_ids':[]},reason='x',reason_code='allowed_to_ignore')
    with pytest.raises(ValueError):
        make_review(p,{'ids':['a'],'region_ids':[]},reason=' ',reason_code='content_review')


def test_review_defaults_keep_old_calls_working():
    from backend.worker.region_reader import Review
    old=Review(1,('a',),(),(0,0,1,1),'reason')
    assert old.candidate_content is None and old.reason_code=='content_review' and old.source_ref is None


# --- render failures stay local ---

@pytest.mark.asyncio
async def test_preview_render_failure_keeps_text_and_makes_page_review():
    async def broken(ids=None,*,bbox=None):raise PageRenderError('preview broken')
    async def unexpected(*args,**kwargs):raise AssertionError('no model call without a preview')
    result=await read_page(page(),decide=unexpected,render=broken)
    assert result.page.calls==0
    assert [r.reason_code for r in result.reviews]==['render_failure']
    assert set(result.reviews[0].ids)=={'a','b'}
    assert [g.text for g in result.page.groups]==['23','45']
    assert result.failure_kind=='render_failure'


@pytest.mark.asyncio
async def test_range_crop_failure_only_affects_that_range_and_costs_no_call():
    async def partly(ids=None,*,bbox=None):
        if ids and ids[0]=='ra':raise PageRenderError('crop broken')
        return await render(ids,bbox=bbox)
    calls=[]
    replies=iter([layout(),formula('4^5')])
    async def decide(messages,**kwargs):
        calls.append(kwargs['phase'])
        return next(replies)
    result=await read_page(page(),decide=decide,render=partly,max_calls=6)
    assert calls==['layout','region']
    assert result.page.calls==2 and len(result.page.trace)==2
    assert [(r.ids,r.reason_code) for r in result.reviews]==[(('a',),'render_failure')]
    assert [g.text for g in result.page.groups]==['23','4^5']


@pytest.mark.asyncio
async def test_model_requested_crop_failure_is_returned_to_the_model():
    async def partly(ids=None,*,bbox=None):
        if bbox:raise PageRenderError('zoom broken')
        return await render(ids,bbox=bbox)
    seen=[]
    replies=iter([{'action':'layout','regions':[{'ids':['a'],'region_ids':['ra']}],'decorations':['rb']},
                  {'action':'inspect','bbox':[5,5,90,40]},review('Still unclear')])
    async def decide(messages,**kwargs):
        seen.append(messages)
        return next(replies)
    result=await read_page(page(),decide=decide,render=partly,max_calls=6)
    assert 'zoom broken' in json.loads(seen[2][2]['content'])['error']
    assert [r.reason_code for r in result.reviews]==['content_review']
    assert result.page.calls==3


@pytest.mark.asyncio
async def test_unexpected_render_error_propagates():
    async def buggy(ids=None,*,bbox=None):
        if ids:raise RuntimeError('programming error')
        return await render(ids,bbox=bbox)
    with pytest.raises(RuntimeError):
        await run([layout(),formula()],render=buggy)


# --- budget and service failures ---

@pytest.mark.asyncio
async def test_inspection_and_corrections_never_exceed_six_calls():
    calls=[]
    async def decide(messages,**kwargs):
        calls.append(1)
        return layout() if len(calls)==1 else {'action':'inspect'}
    result=await read_page(page(),decide=decide,render=render,max_calls=6)
    assert len(calls)==6 and result.page.calls==6
    assert [r.reason_code for r in result.reviews]==['budget_exhausted','budget_exhausted']


@pytest.mark.asyncio
async def test_timeout_counts_as_a_call_and_keeps_finished_ranges():
    replies=iter([layout(),formula(),TimeoutError()])
    async def decide(messages,**kwargs):
        value=next(replies)
        if isinstance(value,BaseException):raise value
        return value
    result=await read_page(page(),decide=decide,render=render,max_calls=6)
    assert result.page.calls==3
    assert result.failure_kind=='model_request_failure'
    assert [g.text for g in result.page.groups]==['2^3','45']
    assert [(r.ids,r.reason_code) for r in result.reviews]==[(('b',),'model_request_failure')]


@pytest.mark.asyncio
async def test_connection_failure_before_layout_keeps_text_by_native_block():
    p=replace(page(),lines=(NativeLine('a',(10,10,80,30),'23',block_id='b1'),
                            NativeLine('b',(300,10,380,30),'45',block_id='b2')))
    async def decide(messages,**kwargs):raise connection_error()
    result=await read_page(p,decide=decide,render=render,max_calls=6)
    assert result.page.calls==1
    assert [r.reason_code for r in result.reviews]==['model_request_failure']
    assert [g.ids for g in result.page.groups]==[('a',),('b',)]


@pytest.mark.asyncio
async def test_three_consecutive_service_failures_stop_model_calls_but_keep_text(tmp_path):
    requests=[]
    async def create(**kwargs):
        requests.append(1)
        raise connection_error()
    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    path=tmp_path/'source.pdf';path.write_bytes(b'fixture')
    from backend.worker.region_reader import make_region_reader
    reader=make_region_reader(client,'fake',render_fn=render)
    pages=[replace(page(),page_no=n) for n in range(1,6)]
    read,report=await reader(str(path),prepared_pages=pages)
    assert len(requests)==3
    assert [p.source.page_no for p in read]==[1,2,3,4,5]
    assert all({i for g in p.groups for i in g.ids}=={'a','b'} for p in read)
    results=report['page_results']
    assert [r['status'] for r in results[3:]]==['not_processed','not_processed']
    assert all(r['reason']=='model_request_failure' and r['visual_processed'] is False for r in results[3:])
    assert {r['page_no'] for r in report['reviews']}=={1,2,3}


@pytest.mark.asyncio
async def test_adapter_disables_client_retries(tmp_path):
    options={}
    async def create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
            message=SimpleNamespace(content=json.dumps({'action':'layout','regions':[],'decorations':['ra','rb']})))],usage=None)
    completions=SimpleNamespace(create=create)
    configured=SimpleNamespace(chat=SimpleNamespace(completions=completions))
    def with_options(**kwargs):
        options.update(kwargs)
        return configured
    client=SimpleNamespace(with_options=with_options)
    path=tmp_path/'source.pdf';path.write_bytes(b'fixture')
    from backend.worker.region_reader import make_region_reader
    await make_region_reader(client,'fake',render_fn=render)(str(path),prepared_pages=[page()])
    assert options=={'max_retries':0,'timeout':120}


@pytest.mark.asyncio
async def test_review_never_changes_native_code_characters_or_indentation():
    code=(NativeLine('a',(10,10,80,20),'if (x) {',char_width=5),
          NativeLine('b',(30,25,120,35),'return 1;',char_width=5),
          NativeLine('c',(10,40,20,50),'}',char_width=5))
    p=NativePage(1,600,400,code,(NativeRegion('ra',(10,10,120,50),'drawing'),))
    async def crop(ids=None,*,bbox=None):
        from backend.worker.visual_harness import RenderedView
        return RenderedView(b'png',tuple(bbox) if bbox else (2,2,128,58) if ids else (0,0,600,400))
    result=await run([{'action':'layout','regions':[{'ids':['a','b','c'],'region_ids':['ra']}]},
                      review('Picture of code needs a person',candidate_content='if (x) { return 2; }')],p,render=crop)
    assert result.page.groups[0].text=='if (x) {\n    return 1;\n}'
    assert result.page.groups[0].kind=='code'


# --- v1.8: a description line that repeats a native line is not searchable --

def code_page():
    lines=(NativeLine('a',(10,10,200,20),'int *tab = malloc(n * sizeof(int ));',char_width=5),
           NativeLine('b',(10,25,200,35),'fprintf(stderr , "plus␣de␣memoire\\n");',char_width=5),
           NativeLine('c',(300,10,380,30),'45'))
    return NativePage(1,600,400,lines,(NativeRegion('ra',(5,5,205,40),'drawing'),NativeRegion('rb',(300,10,380,30),'drawing')))


async def code_crop(ids=None,*,bbox=None):
    from backend.worker.visual_harness import RenderedView
    return RenderedView(b'png',tuple(bbox) if bbox else (0,0,210,45) if ids else (0,0,600,400))


CODE_LAYOUT={'action':'layout','regions':[{'ids':['a','b'],'region_ids':['ra']}],'decorations':['rb']}


def figure(content):
    return {'action':'submit','outcome':'supplement','kind':'figure','content':content}


@pytest.mark.asyncio
async def test_description_lines_repeating_native_lines_are_not_searchable():
    described=('A box with an allocation example.\n'
               'int *tab = malloc(n * sizeof(int));\n'
               '  fprintf(stderr, "plus␣de␣memoire\\n");\n'
               'The check stops the program when memory is missing.')
    result=await run([CODE_LAYOUT,figure(described)],code_page(),render=code_crop)
    assert not result.reviews and result.page.calls==2
    group=result.page.groups[0]
    assert group.text.startswith('A box with an allocation example.\nThe check stops the program')
    # The program's copy of the code is still there, exactly once.
    assert group.text.count('malloc(')==1 and group.text.count('memoire')==1
    assert 'int *tab = malloc(n * sizeof(int ));' in group.text
    # The model's full wording stays in the report, outside search.
    assert group.visual['model_description']==described
    assert len(group.visual['removed_repeated_lines'])==2


@pytest.mark.asyncio
async def test_code_inside_a_sentence_or_changed_code_is_kept():
    described='It calls fprintf(stderr, "plus de memoire\\n"); when malloc fails.\nfprintf(stderr, "plus de memoire\\n");'
    result=await run([CODE_LAYOUT,figure(described)],code_page(),render=code_crop)
    # Only an exact repeat of a native line is removed; a changed copy is not recognised.
    assert result.page.groups[0].text.startswith(described)
    assert result.page.groups[0].visual['removed_repeated_lines']==[]


@pytest.mark.asyncio
async def test_description_that_only_repeats_native_lines_is_returned():
    seen=[]
    replies=iter([CODE_LAYOUT,figure('int *tab = malloc(n * sizeof(int ));'),figure('A box with an allocation example.')])
    async def decide(messages,**kwargs):
        seen.append(messages)
        return next(replies)
    result=await read_page(code_page(),decide=decide,render=code_crop,max_calls=6)
    assert 'only repeats' in json.loads(seen[2][2]['content'])['error']
    assert result.page.groups[0].text.startswith('A box with an allocation example.')


def test_prompt_is_back_to_the_v16_wording_on_code():
    from backend.worker.region_reader import PROMPT
    assert 'never retype' not in PROMPT.lower()
    assert 'Images containing code' in PROMPT
