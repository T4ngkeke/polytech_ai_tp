import pytest
import pymupdf
from backend.worker.page_reader import NativePage, NativeLine, NativeRegion, PageReadError
from backend.worker.visual_regions import extract_regions, read_visual


def empty_visual():
    return {'latex':'','rows':[],'nodes':[],'edges':[]}


def answer(kind='formula', text='2^{3}'):
    return dict(action='finish', ids=[], bbox=[], decorations=[], unreadable=[], groups=[
        dict(kind=kind, ids=['l'], region_ids=['r'], visual={**empty_visual(),'latex':text})])


@pytest.mark.asyncio
async def test_formula_requires_crop_and_preserves_source():
    page=NativePage(1,600,400,(NativeLine('l',(10,10,100,30),'23'),), (NativeRegion('r',(10,10,100,30),'drawing'),))
    seen=[]
    async def render(ids, **kwargs): seen.append(ids); return b'png'
    async def decide(messages):
        if len(seen)==1: return dict(action='inspect',ids=['r'],bbox=[],groups=[],decorations=[],unreadable=[])
        return answer()
    result=await read_visual(page,decide=decide,render=render)
    assert result.groups[0].text == '2^{3}'
    assert result.groups[0].ids == ('l',)
    assert result.calls == 2
    assert seen == [None,['r']]


@pytest.mark.asyncio
async def test_uninspected_visual_and_code_rewrite_rejected():
    page=NativePage(1,600,400,(NativeLine('l',(10,10,100,30),'free(p);'),), (NativeRegion('r',(10,10,100,30),'drawing'),))
    async def render(ids, **kw): return b'png'
    for value in [answer(), answer('code')]:
        async def decide(messages): return value
        with pytest.raises(PageReadError): await read_visual(page,decide=decide,render=render,max_calls=2)


def test_vector_clusters_keep_text_boxes_and_small_arrows():
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=400,height=300)
        p.draw_rect((10,10,100,60)); p.insert_text((20,35),'start')
        p.draw_line((100,35),(130,35)); p.draw_line((125,30),(130,35))
        regions=extract_regions(p,1)
        assert len(regions)==1
        assert regions[0].bbox[2]>=130

@pytest.mark.asyncio
async def test_registered_bbox_and_unknown_regions():
    page=NativePage(1,600,400,(NativeLine('l',(10,10,100,30),'23'),))
    seen=[];region_id=None
    async def render(ids,**kw):
        nonlocal region_id
        seen.append(ids)
        if ids: region_id=ids[0]
        return b'png'
    async def decide(messages):
        if len(seen)==1: return dict(action='inspect',ids=[],bbox=[9,9,110,40],groups=[],decorations=[],unreadable=[])
        value=answer(); value['groups'][0]['region_ids']=[region_id];return value
    read=await read_visual(page,decide=decide,render=render)
    assert read.groups[0].text=='2^{3}' and read.source.regions
    async def bad(messages):
        return dict(action='inspect',ids=[],bbox=[-1,0,100,40],groups=[],decorations=[],unreadable=[])
    with pytest.raises(PageReadError,match='bbox'):await read_visual(page,decide=bad,render=render,max_calls=1)


def test_table_and_diagram_structure():
    from backend.worker.visual_regions import visual_text
    group=answer('table')['groups'][0]
    group['visual']={**empty_visual(),'rows':[['A','B'],['1','2']]}
    assert visual_text(group)=='A | B\n1 | 2'
    group['visual']['rows'][1]=['1']
    with pytest.raises(PageReadError):visual_text(group)
    group['kind']='diagram'
    group['visual']={**empty_visual(),'nodes':[{'id':'a','text':'test'},{'id':'b','text':'loop'}], 'edges':[{'from':'a','to':'b','label':'yes'}]}
    assert 'a -> b: yes' in visual_text(group)
    group['visual']['edges'][0]['to']='missing'
    with pytest.raises(PageReadError):visual_text(group)


def test_embedded_image_and_full_page_background():
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=400,height=300)
        p.draw_rect(p.rect,fill=(1,1,1))
        pix=pymupdf.Pixmap(pymupdf.csRGB,pymupdf.IRect(0,0,10,10));pix.clear_with(100)
        p.insert_image((20,20,80,80),pixmap=pix)
        regions=extract_regions(p,1)
        assert {r.kind for r in regions}=={'image','background'}

@pytest.mark.asyncio
async def test_coverage_feedback_names_missing_region():
    page=NativePage(1,600,400,(NativeLine('l',(10,10,100,30),'title'),),(NativeRegion('r',(10,10,100,30),'drawing'),))
    seen=[]
    async def render(ids,**kw):return b'png'
    async def decide(messages):
        seen.append(str(messages[-1]))
        return dict(action='finish',ids=[],bbox=[],decorations=[],unreadable=[],groups=[dict(kind='text',ids=['l'],region_ids=[],visual=empty_visual())])
    with pytest.raises(PageReadError): await read_visual(page,decide=decide,render=render,max_calls=2)
    assert "Missing regions: ['r']" in seen[-1]

@pytest.mark.asyncio
async def test_inspect_ids_with_containing_bbox_is_unambiguous():
    page=NativePage(1,600,400,(NativeLine('l',(10,10,100,30),'23'),),(NativeRegion('r',(10,10,100,30),'drawing'),))
    calls=[]
    async def render(ids,**kwargs): calls.append(ids);return b'png'
    async def decide(messages):
        if len(calls)==1:return dict(action='inspect',ids=['r'],bbox=[9,9,110,40],groups=[],decorations=[],unreadable=[])
        v=answer();v['decorations']=[i for i in calls[-1] if i!='r'];return v
    result=await read_visual(page,decide=decide,render=render,max_calls=2)
    assert result.groups[0].text=='2^{3}'

@pytest.mark.asyncio
async def test_crop_also_covers_registered_regions_inside_it():
    page=NativePage(1,600,400,(NativeLine('l',(10,10,100,30),'23'),),
        (NativeRegion('r',(10,10,100,30),'drawing'),NativeRegion('outer',(0,0,200,80),'drawing')))
    seen=[]
    async def render(ids,**kw):seen.append(ids);return b'png'
    async def decide(messages):
        if len(seen)==1:return dict(action='inspect',ids=['outer'],bbox=[],groups=[],decorations=[],unreadable=[])
        value=answer();value['decorations']=['outer'];return value
    result=await read_visual(page,decide=decide,render=render,max_calls=2)
    assert result.groups[0].text=='2^{3}'

@pytest.mark.asyncio
async def test_timeout_carries_attempt_trace():
    page=NativePage(1,600,400,(NativeLine('l',(10,10,100,30),'source'),))
    async def render(ids,**kw):return b'png'
    async def decide(messages):raise TimeoutError('model timeout')
    with pytest.raises(TimeoutError) as caught: await read_visual(page,decide=decide,render=render)
    assert len(caught.value.trace)==1
    assert caught.value.trace[0]['call']==1
    assert caught.value.trace[0]['seconds']>=0


def test_wire_actions_cannot_mix_inspection_and_finish_fields():
    from backend.worker.visual_regions import decode_wire
    assert decode_wire({'next_step':{'action':'inspect_ids','ids':['r']}})['ids']==['r']
    with pytest.raises(PageReadError):
        decode_wire({'next_step':{'action':'inspect_ids','ids':['r'],'bbox':[0,0,100,100]}})
    with pytest.raises(PageReadError):
        decode_wire({'next_step':{'action':'finish','groups':[],'decorations':[],'unreadable':[],'ids':['r']}})
    result=decode_wire({'next_step':{'action':'finish','groups':[],'decorations':['r'],'unreadable':[]}})
    assert result==dict(action='finish',ids=[],bbox=[],groups=[],decorations=['r'],unreadable=[])

@pytest.mark.asyncio
async def test_uninspected_feedback_names_group_and_regions():
    """A vague rejection made the model resend the same answer up to four times."""
    page=NativePage(1,600,400,(NativeLine('l',(10,10,100,30),'23'),),
        (NativeRegion('r1',(10,10,100,30),'drawing'),NativeRegion('r2',(200,10,300,30),'drawing')))
    seen=[]
    async def render(ids,**kw):return b'png'
    async def decide(messages):
        seen.append(str(messages[-1]))
        if len(seen)==1:return dict(action='inspect',ids=['r1'],bbox=[],groups=[],decorations=[],unreadable=[])
        value=answer();value['groups'][0]['region_ids']=['r2'];value['decorations']=['r1'];return value
    with pytest.raises(PageReadError): await read_visual(page,decide=decide,render=render,max_calls=3)
    assert "not inspected=['r2']" in seen[-1] and "inspected so far=['r1']" in seen[-1]


def test_format_feedback_names_kind_and_fields():
    """Prose and a display formula in one box must be split, not rejected blindly."""
    from backend.worker.visual_regions import visual_text
    with pytest.raises(PageReadError) as caught: visual_text(answer('text')['groups'][0])
    message=str(caught.value)
    assert "'text'" in message and 'latex' in message and 'own formula group' in message


@pytest.mark.asyncio
async def test_table_rules_crop_covers_the_text_between_them():
    """Beamer draws a table as hairlines; its text sits between them, inside the rendered crop."""
    page=NativePage(1,600,400,
        (NativeLine('l7',(80,68,300,78),'int'),NativeLine('l8',(80,82,300,92),'un nombre entier')),
        (NativeRegion('r2',(78.7,66.7,374.9,66.8),'drawing'),
         NativeRegion('r3',(78.7,80.7,374.9,80.8),'drawing'),
         NativeRegion('r4',(78.7,135.2,374.9,135.3),'drawing')))
    seen=[]
    async def render(ids,**kw): seen.append(ids); return b'png'
    async def decide(messages):
        if len(seen)==1: return dict(action='inspect',ids=['r2','r3','r4'],bbox=[],groups=[],decorations=[],unreadable=[])
        return dict(action='finish',ids=[],bbox=[],decorations=[],unreadable=[],groups=[
            dict(kind='table',ids=['l7','l8'],region_ids=['r2','r3','r4'],
                 visual={**empty_visual(),'rows':[['type','sens'],['int','un nombre entier']]})])
    result=await read_visual(page,decide=decide,render=render,max_calls=3)
    assert result.groups[0].text=='type | sens\nint | un nombre entier'


@pytest.mark.asyncio
async def test_outside_crop_feedback_names_lines_and_crops():
    """Two distant crops must not merge into one box covering the untouched middle."""
    page=NativePage(1,600,400,
        (NativeLine('l0',(10,10,100,30),'23'),NativeLine('l1',(10,300,100,320),'x')),
        (NativeRegion('r0',(10,10,100,30),'drawing'),NativeRegion('r1',(10,350,100,370),'drawing')))
    seen=[]
    async def render(ids,**kw): seen.append(ids); return b'png'
    async def decide(messages):
        if len(seen)==1: return dict(action='inspect',ids=['r0'],bbox=[],groups=[],decorations=[],unreadable=[])
        if len(seen)==2: return dict(action='inspect',ids=['r1'],bbox=[],groups=[],decorations=[],unreadable=[])
        return dict(action='finish',ids=[],bbox=[],decorations=[],unreadable=[],groups=[
            dict(kind='formula',ids=['l0','l1'],region_ids=['r0','r1'],visual={**empty_visual(),'latex':'2^{3}'})])
    with pytest.raises(PageReadError) as caught:
        await read_visual(page,decide=decide,render=render,max_calls=3)
    message=str(caught.value)
    assert "'l1'" in message and 'crops' in message


@pytest.mark.asyncio
async def test_inspecting_a_native_line_keeps_the_name_the_model_asked_with():
    """We rename an inspected line to a registered region; the model still knows it as 'l'."""
    page=NativePage(1,600,400,(NativeLine('l',(10,10,100,30),'Nombre = (-1)S'),))
    async def render(ids,**kw): return b'png'
    async def decide_line(messages):
        if len(messages)<3: return dict(action='inspect',ids=['l'],bbox=[],groups=[],decorations=[],unreadable=[])
        value=answer(); value['groups'][0]['region_ids']=['l']; return value
    result=await read_visual(page,decide=decide_line,render=render,max_calls=3)
    assert result.groups[0].text=='2^{3}'


@pytest.mark.asyncio
async def test_both_names_for_one_area_count_once():
    """Naming the same area old-style and new-style is not a repeated region."""
    page=NativePage(1,600,400,(NativeLine('l',(10,10,100,30),'23'),))
    registered=[]
    async def render(ids,**kw): registered.extend(ids or []); return b'png'
    async def decide(messages):
        if not registered: return dict(action='inspect',ids=['l'],bbox=[],groups=[],decorations=[],unreadable=[])
        value=answer(); value['groups'][0]['region_ids']=['l',registered[0]]; return value
    result=await read_visual(page,decide=decide,render=render,max_calls=3)
    assert result.groups[0].region_ids==(registered[0],)
