"""V4 invariants: observations are evidence, never new content obligations."""
import pytest
from backend.worker.page_reader import NativeLine, NativeRegion, NativePage, PageReadError
from backend.worker.visual_harness import read_page, RenderedView


def group(ids, regions=(), observations=(), text='2^{3}'):
    return {'kind':'formula','ids':list(ids),'region_ids':list(regions),
            'observation_ids':list(observations),'latex':text}


def finish(groups, decorations=()):
    return {'action':'finish','groups':groups,'decorations':list(decorations),'unreadable':[]}


def page():
    return NativePage(1,600,400,(NativeLine('a',(10,10,80,30),'23'),
        NativeLine('b',(300,10,380,30),'45')),
        (NativeRegion('ra',(10,10,80,30),'drawing'),NativeRegion('rb',(300,10,380,30),'drawing')))


async def render(ids=None, *, bbox=None):
    boxes={'ra':(2,2,88,38),'rb':(292,2,388,38),'a':(2,2,88,38)}
    return RenderedView(b'png', tuple(bbox) if bbox else boxes[ids[0]] if ids else (0,0,600,400))


async def run(p, actions, **kwargs):
    replies=iter(actions)
    async def decide(messages, **kwargs):return next(replies)
    return await read_page(p,decide=decide,render=render,max_calls=len(actions),**kwargs)


@pytest.mark.asyncio
async def test_observation_does_not_create_content_and_native_alias_works():
    p=NativePage(1,600,400,(NativeLine('a',(10,10,80,30),'23'),))
    r=await run(p,[{'action':'inspect_ids','ids':['a']},finish([group(['a'],observations=['o1'])])])
    assert not r.source.regions
    assert r.groups[0].text=='2^{3}'
    assert r.observations[0]['bbox']==(2,2,88,38)


@pytest.mark.asyncio
async def test_left_evidence_cannot_replace_right_text_even_if_both_seen():
    with pytest.raises(PageReadError,match='outside|associated'):
        await run(page(),[{'action':'inspect_ids','ids':['ra']},{'action':'inspect_ids','ids':['rb']},
            finish([group(['b'],['ra'],['o1']),{'kind':'text','ids':['a'],'region_ids':[]}],['rb'])])


@pytest.mark.asyncio
async def test_two_related_observations_can_support_one_result():
    r=await run(page(),[{'action':'inspect_ids','ids':['ra']},{'action':'inspect_ids','ids':['rb']},
        finish([group(['a','b'],['ra','rb'],['o1','o2'])])])
    assert r.groups[0].text=='2^{3}'


@pytest.mark.asyncio
async def test_replace_only_bad_group_preserves_good_group():
    actions=[{'action':'inspect_ids','ids':['ra']},finish([
        group(['a'],['ra'],['missing']),{'kind':'text','ids':['b'],'region_ids':['rb']}]),
        {'action':'replace_group','group_id':'g1','group':group(['a'],['ra'],['o1'])}]
    r=await run(page(),actions)
    assert [g.text for g in r.groups]==['2^{3}','45']
    assert r.calls==3


@pytest.mark.asyncio
async def test_replace_cannot_steal_other_groups_source():
    with pytest.raises(PageReadError,match='owned|belongs'):
        await run(page(),[finish([group(['a'],['ra'],['missing']),{'kind':'text','ids':['b'],'region_ids':['rb']}]),
            {'action':'replace_group','group_id':'g1','group':group(['b'],['ra'],['missing'])}])


def uml_page():
    return NativePage(1,600,400,(NativeLine('a',(10,10,80,30),'afficher() : void'),),
        (NativeRegion('ra',(10,10,80,30),'drawing'),))


def figure(ids=('a',), description='Diagramme de classes : Rapport hérite de Document.'):
    return {'kind':'figure','ids':list(ids),'region_ids':['ra'],'observation_ids':['o1'],'description':description}


@pytest.mark.asyncio
async def test_uml_is_a_figure_with_copied_labels_and_a_short_description():
    """UML is rare: no node/edge transcription, only labels as printed plus what it shows."""
    r=await run(uml_page(),[{'action':'inspect_ids','ids':['ra']},finish([figure()])])
    assert r.groups[0].kind=='figure'
    assert 'afficher() : void' in r.groups[0].text
    assert 'Rapport hérite de Document' in r.groups[0].text


@pytest.mark.asyncio
async def test_figure_description_needs_a_screenshot():
    with pytest.raises(PageReadError,match='observation_ids'):
        await run(uml_page(),[finish([figure()])])


@pytest.mark.asyncio
@pytest.mark.parametrize('description',['',' ','x'*601])
async def test_figure_description_is_short_and_nonempty(description):
    with pytest.raises(PageReadError,match='description'):
        await run(uml_page(),[{'action':'inspect_ids','ids':['ra']},finish([figure(description=description)])])


@pytest.mark.asyncio
async def test_diagram_structure_is_no_longer_offered():
    g={'kind':'diagram','ids':['a'],'region_ids':['ra'],'observation_ids':['o1'],
       'nodes':[{'id':'s','title':'Student','sections':[]}],'edges':[]}
    with pytest.raises(PageReadError,match='figure'):
        await run(uml_page(),[{'action':'inspect_ids','ids':['ra']},finish([g])])


def test_geometry_signature_distinguishes_same_bbox_paths():
    import pymupdf
    from backend.worker.visual_geometry import annotate_regions
    from backend.worker.visual_regions import extract_regions
    with pymupdf.open() as pdf:
        a=pdf.new_page(width=400,height=300);a.draw_line((10,10),(100,100))
        b=pdf.new_page(width=400,height=300);b.draw_line((10,100),(100,10))
        a,b=pdf[0],pdf[1]
        ra=annotate_regions(a,extract_regions(a,1));rb=annotate_regions(b,extract_regions(b,2))
        assert ra[0].bbox==rb[0].bbox
        assert ra[0].signature!=rb[0].signature


def test_repeat_counts_pages_not_duplicate_occurrences():
    from dataclasses import replace
    from backend.worker.visual_geometry import repeat_hints
    r=NativeRegion('r',(0,0,10,10),'drawing',signature='same')
    pages=[NativePage(i,100,100,(),(r,replace(r,id='r2'))) for i in range(1,6)]
    hinted=repeat_hints(pages)
    assert hinted[0].regions[0].occurrence_pages==5
    assert hinted[0].regions[0].template_hint=='suspected_template'
    assert hinted[0].regions[0].total_pages==5


def test_renderer_returns_actual_crop_rectangle(tmp_path):
    import pymupdf
    from backend.worker.page_reader import render_observation
    path=tmp_path/'page.pdf'
    with pymupdf.open() as pdf:
        pdf.new_page(width=600,height=400);pdf.save(path)
    view=render_observation(str(path),page(),['ra'])
    assert view.bbox==(2,2,88,38)
    assert view.png.startswith(b'\x89PNG')


@pytest.mark.asyncio
async def test_fingerprint_ignores_observation_box_changes():
    from backend.worker.page_reader import chunk_read_pages
    from backend.worker.ingest import _source_fingerprint
    p=NativePage(1,600,400,(NativeLine('a',(10,10,80,30),'23'),))
    chunks=[]
    for box in ([1,1,90,40],[0,0,100,50]):
        r=await run(p,[{'action':'inspect_box','bbox':box},finish([group(['a'],observations=['o1'])])])
        chunks.append(chunk_read_pages([r])[0])
    assert chunks[0].source_ref['version']==2
    assert _source_fingerprint(chunks[0])==_source_fingerprint(chunks[1])


@pytest.mark.asyncio
async def test_same_error_twice_supplies_draft_and_specific_next_step():
    seen=[]
    async def decide(messages, **kwargs):
        seen.append(messages)
        if len(seen)==1:return finish([group(['a'],['ra'],['missing']),{'kind':'text','ids':['b'],'region_ids':['rb']}])
        return {'action':'replace_group','group_id':'g1','group':group(['a'],['ra'],['missing'])}
    with pytest.raises(PageReadError):await read_page(page(),decide=decide,render=render,max_calls=4)
    import json
    feedback=json.loads(seen[-1][-1]['content'])
    assert feedback['repeated_error'] and feedback['draft']['g2']['ids']==['b']


@pytest.mark.asyncio
async def test_confirmed_template_is_recorded_not_deleted():
    from dataclasses import replace
    p=page();p=replace(p,regions=(replace(p.regions[0],template_hint='confirmed_decoration'),p.regions[1]))
    r=await run(p,[finish([{'kind':'text','ids':['a','b'],'region_ids':['rb']}])])
    assert r.decorations==('ra',)
    assert len(r.source.regions)==2 and '23' in r.groups[0].text


def test_background_signature_does_not_absorb_foreground_drawings():
    import pymupdf
    from backend.worker.visual_geometry import annotate_regions
    from backend.worker.visual_regions import extract_regions
    with pymupdf.open() as pdf:
        for end in (100,200):
            p=pdf.new_page(width=400,height=300)
            p.draw_rect(p.rect,fill=(1,1,1));p.draw_line((10,10),(end,100))
        signatures=[]
        for i in range(2):
            p=pdf[i];regions=annotate_regions(p,extract_regions(p,i+1))
            signatures.append(next(r.signature for r in regions if r.kind=='background'))
        assert signatures[0]==signatures[1]


@pytest.mark.asyncio
async def test_invalid_patch_cannot_drop_previously_owned_native_line():
    p=page();seen=[]
    async def decide(messages, **kwargs):
        seen.append(messages)
        if len(seen)==1:return finish([group(['a','b'],['ra','rb'],['missing'])])
        return {'action':'replace_group','group_id':'g1','group':{'kind':'text','ids':['a'],'region_ids':['ra','rb']}}
    with pytest.raises(PageReadError,match='drop|split'):
        await read_page(p,decide=decide,render=render,max_calls=3)
    import json
    assert json.loads(seen[-1][-1]['content'])['draft']['g1']['ids']==['a','b']


def test_available_actions_follow_draft_state():
    from backend.worker.visual_harness import response_format
    schema=response_format(True,draft_exists=False)['json_schema']['schema']['properties']['next_step']['anyOf']
    assert all(a['properties']['action']['enum']!=['replace_group'] for a in schema)
    finish_schema=next(a for a in schema if a['properties']['action']['enum']==['finish'])
    assert finish_schema['properties']['groups']['minItems']==1
    schema=response_format(True,draft_exists=True)['json_schema']['schema']['properties']['next_step']['anyOf']
    finish_schema=next(a for a in schema if a['properties']['action']['enum']==['finish'])
    assert finish_schema['properties']['groups']['maxItems']==0


@pytest.mark.asyncio
async def test_missing_observation_feedback_suggests_inspect_not_resubmit():
    seen=[]
    async def decide(messages, **kwargs):
        seen.append(messages)
        if len(seen)==1:return finish([group(['a'],['ra'],[]),{'kind':'text','ids':['b'],'region_ids':['rb']}])
        return {'action':'inspect_ids','ids':['ra']}
    with pytest.raises(PageReadError):await read_page(page(),decide=decide,render=render,max_calls=2)
    import json
    feedback=json.loads(seen[-1][-1]['content'])
    assert feedback['suggested_action']=={'action':'inspect_ids','ids':['ra']}
    assert 'inspect' in feedback['next'].lower()



def test_schema_limits_ids_to_their_actual_channel():
    from backend.worker.visual_harness import response_format
    context={'native_ids':['l1'],'region_ids':['r1'],'observation_ids':['o1'],'group_ids':['g1']}
    schema=response_format(True,draft_exists=True,context=context)['json_schema']['schema']['properties']['next_step']['anyOf']
    patch=next(a for a in schema if a['properties']['action']['enum']==['replace_group'])
    assert patch['properties']['group_id']['enum']==['g1']
    formula=next(a for a in patch['properties']['group']['anyOf'] if a['properties']['kind']['enum']==['formula'])
    for field,expected in [('ids',['l1']),('region_ids',['r1']),('observation_ids',['o1'])]:
        assert formula['properties'][field]['items']['enum']==expected
    finish=next(a for a in schema if a['properties']['action']['enum']==['finish'])
    assert finish['properties']['decorations']['items']['enum']==['r1']


def test_background_alone_cannot_prove_identity_of_discovered_graphic():
    from backend.worker.visual_sources import source_ref
    from backend.worker.page_reader import ReadGroup
    p=NativePage(1,600,400,(),(NativeRegion('bg',(0,0,600,400),'background'),
        NativeRegion('new',(10,10,100,100),'discovered',source_ids=('bg',))))
    ref=source_ref(p,ReadGroup('diagram',(),'image text',('new',)),1)
    assert ref['identity_uncertain'] is True
