import asyncio
import pytest
import pymupdf
from scripts.docling_benchmark.core import PageResult, Segment, assess, bind_review, validate_review, output_hash, picture_result
from scripts.docling_benchmark.native import rebuild, check_generated


def page():
    return PageResult(file_sha256='abc', page_no=6, implementation='test', segments=[Segment('table', 'Total : 22h30.', {'bbox':[0,0,20,20]})])


def test_backup_is_not_searchable():
    p=page()
    assert '22h30' not in p.searchable_text
    assert assess(p, required=['22h30']) == 'missing'


def test_contradiction_does_not_pass():
    p=page(); p.segments[0].content='arrow points to parent; arrow points to child'
    assert assess(p, required=['points to parent'], forbidden=['points to child']) == 'mixed'


def test_hash_invalidates_review():
    p=page(); review=bind_review(p, [], reviewer='test', kind='model')
    assert validate_review(p, review)
    p.segments[0].content='changed'
    with pytest.raises(ValueError): validate_review(p, review)


def test_candidate_never_searchable():
    p=page()
    asyncio.run(picture_result(p, {'bbox':[0,0,20,20]}, lambda: reply({'outcome':'review','reason':'arrow unclear','candidate':'invented'})))
    assert 'invented' not in p.searchable_text
    assert len(p.reviews)==1 and p.status=='partial'


async def reply(value): return value


@pytest.mark.parametrize('value',[{'outcome':'review','reason':''},{'outcome':'supplement','content':''},{'outcome':'other'},'bad'])
def test_bad_picture_response_review(value):
    p=page();asyncio.run(picture_result(p, {'bbox':[0,0,20,20]}, lambda: reply(value)))
    assert len(p.reviews)==1 and p.calls==1


def test_picture_timeout_once():
    async def slow(): raise TimeoutError('timeout')
    p=page();asyncio.run(picture_result(p, {'bbox':[0,0,20,20]}, slow))
    assert p.calls==1 and p.reviews[0]['reason_code']=='model_request_failure'


def test_picture_supplement_preserves_native():
    p=page();asyncio.run(picture_result(p, {'bbox':[0,0,20,20]}, lambda: reply({'outcome':'supplement','content':'a tree'})))
    assert p.segments[0].content=='table' and 'a tree' in p.searchable_text


def test_native_code_spacing_and_indent():
    d=pymupdf.open();p=d.new_page()
    p.insert_text((50,50),'if True:\n    print("a  b")\n    return_value = 3',fontname='cour',fontsize=10)
    r=rebuild(p,[45,35,300,100])
    assert not r.issues
    assert r.text=='if True:\n    print("a  b")\n    return_value = 3'


def test_empty_and_invalid_boxes():
    d=pymupdf.open();p=d.new_page()
    assert rebuild(p,[0,0,20,20]).issues
    with pytest.raises(ValueError): rebuild(p,[-1,0,30,20])
    with pytest.raises(ValueError): rebuild(p,[20,0,10,20])


def test_two_columns_and_inline_comments():
    d=pymupdf.open();p=d.new_page()
    p.insert_text((50,50),'a = 1  # value\nb = 2',fontname='cour',fontsize=10)
    p.insert_text((300,50),'x = 3\ny = 4',fontname='cour',fontsize=10)
    r=rebuild(p,[45,35,400,90])
    assert 'a = 1  # value' in r.text
    assert r.text.index('b = 2')<r.text.index('x = 3')


def test_generation_repetition_detected():
    assert check_generated('c ' * 100)
    assert not check_generated('x = x + 1')


def test_source_overlap_not_silently_replaced():
    from scripts.docling_benchmark.adapters import overlapping
    assert overlapping([0,0,20,20], [[0,0,20,20]])
    assert not overlapping([30,0,50,20], [[0,0,20,20]])


def test_overlapping_native_characters_review():
    d=pymupdf.open();p=d.new_page()
    p.insert_text((50,50),'abc',fontname='cour')
    p.insert_text((50,50),'abc',fontname='cour')
    assert rebuild(p,[40,30,100,70]).issues==['Overlapping native glyphs']


def test_mixed_font_sizes_keep_indentation():
    d=pymupdf.open();p=d.new_page()
    p.insert_text((50,50),'x = 1',fontname='cour',fontsize=10)
    p.insert_text((50,80),'y = 2',fontname='cour',fontsize=14)
    assert rebuild(p,[40,30,200,100]).text=='x = 1\ny = 2'


def test_rotated_code_is_not_guessed():
    d=pymupdf.open();p=d.new_page()
    p.insert_text((50,50),'x = 1');p.set_rotation(90)
    assert rebuild(p,list(p.rect)).issues


def test_frozen_run_cannot_change(tmp_path):
    from scripts.docling_benchmark.suite import freeze
    freeze(tmp_path,{'model':'one'});freeze(tmp_path,{'model':'one'})
    with pytest.raises(ValueError):freeze(tmp_path,{'model':'two'})


def test_suite_has_stable_unique_checks():
    import json
    from scripts.docling_benchmark.suite import HISTORY, load_suite, source_path
    files = json.loads((HISTORY / 'manifest.json').read_text())['files']
    if any(not source_path(name).is_file() for name in files):
        pytest.skip('Requires the local frozen PDF benchmark corpus')
    suite=load_suite()
    ids=[c['check_id'] for entries in suite['checks'].values() for c in entries]
    assert len(ids)==90 and len(set(ids))==90 and len(suite['cases'])==28


def test_report_rejects_stale_and_does_not_invent_score(tmp_path):
    import json
    from scripts.docling_benchmark.report import generate
    folder=tmp_path/'runs'/'test';folder.mkdir(parents=True)
    p=page();path=folder/'example.page.json';path.write_text(json.dumps(p.dump()))
    summary=generate(tmp_path)
    assert summary['test']['graded_pages']==0 and summary['test']['checks']=={}
    review=bind_review(p,[],reviewer='test',kind='model')
    path.with_suffix('.review.json').write_text(json.dumps(review))
    p.segments[0].content='changed';path.write_text(json.dumps(p.dump()))
    with pytest.raises(ValueError):generate(tmp_path)


def test_separate_comment_span_is_not_moved_to_its_own_column():
    d=pymupdf.open();p=d.new_page()
    p.insert_text((50,50),'x = 1',fontname='cour',fontsize=10)
    p.insert_text((250,50),'# explanation',fontname='cour',fontsize=10)
    r=rebuild(p,[40,30,400,70])
    assert r.issues
    assert 'x = 1' in r.text.splitlines()[0] and '# explanation' in r.text.splitlines()[0]


def test_grade_rejects_missing_checks(tmp_path, monkeypatch):
    import json
    from scripts.docling_benchmark import grade
    monkeypatch.setattr(grade, 'load_suite', lambda: {
        'checks': {'main-2-p6': [{'check_id': 'main-2-p6/total'}]}})
    record = grade.record
    f=tmp_path/'main-2-p6.page.json';f.write_text(json.dumps(page().dump()))
    with pytest.raises(ValueError):record(f,{'checks':[],'reviewer':'test','reviewer_kind':'model'})


def test_suite_can_load_another_page_set(tmp_path, monkeypatch):
    import hashlib, json
    from scripts.docling_benchmark import suite as suite_module
    sources = tmp_path / 'sources'
    pdf = sources / 'cours' / 'pdf_files' / 'main-2.pdf'
    pdf.parent.mkdir(parents=True)
    with pymupdf.open() as source:
        source.new_page().insert_text((40, 40), 'Fixture source')
        pdf.write_bytes(source.tobytes())
    monkeypatch.setattr(suite_module, 'ROOT', sources)
    load_suite = suite_module.load_suite
    digest=hashlib.sha256(pdf.read_bytes()).hexdigest()
    (tmp_path/'manifest.json').write_text(json.dumps({'files':{'main-2.pdf':digest},
        'cases':[{'file':'main-2.pdf','page':9,'stem':'main-2-p9','set':'extra'}]}))
    (tmp_path/'checks.json').write_text(json.dumps({'main-2-p9':[{'id':'toc','kind':'text','fact':'x'}]}))
    suite=load_suite(tmp_path)
    assert [c['stem'] for c in suite['cases']]==['main-2-p9']
    assert suite['checks']['main-2-p9'][0]['check_id']=='main-2-p9/toc'
    (tmp_path/'manifest.json').write_text(json.dumps({'files':{'main-2.pdf':'0'*64},'cases':[]}))
    with pytest.raises(ValueError):load_suite(tmp_path)


def test_grade_uses_the_checks_saved_with_the_run(tmp_path):
    import json
    from scripts.docling_benchmark.grade import record
    suite={'checks':{'main-2-p9':[{'id':'toc','check_id':'main-2-p9/toc','kind':'text','fact':'x'}]}}
    (tmp_path/'suite.json').write_text(json.dumps(suite))
    folder=tmp_path/'runs'/'worker-round1';folder.mkdir(parents=True)
    f=folder/'main-2-p9.page.json';f.write_text(json.dumps(page().dump()))
    review=record(f,{'reviewer':'test','reviewer_kind':'model',
                     'checks':[{'check_id':'main-2-p9/toc','result':'correct','evidence':'listed'}]})
    assert review['checks'][0]['check_id']=='main-2-p9/toc'


def test_meter_only_targets_the_two_local_model_servers():
    from scripts.docling_benchmark.telemetry import ModelMeter
    ModelMeter('http://127.0.0.1:18002/v1')
    with pytest.raises(ValueError):ModelMeter('http://127.0.0.1:9999/v1')


def test_meter_accepts_the_mineru_and_paddle_model_servers():
    from scripts.docling_benchmark.telemetry import ModelMeter
    ModelMeter('http://127.0.0.1:18003/v1');ModelMeter('http://127.0.0.1:18004/v1')
    with pytest.raises(ValueError):ModelMeter('http://127.0.0.1:18005/v1')


def test_each_variant_names_the_local_model_server_it_needs():
    from scripts.docling_benchmark.run import side_server, VARIANT_CHOICES
    assert side_server('mineru-hybrid')==('edullm-mineru-vlm','18003')
    assert side_server('paddle-vl')==('edullm-paddle-vl','18004')
    assert side_server('marker-balanced-llm')==('edullm-marker-surya','18002')
    assert side_server('mineru-pipeline') is None and side_server('paddle-structure') is None and side_server('worker') is None
    assert {'mineru-pipeline','mineru-hybrid','paddle-structure','paddle-vl'}<=set(VARIANT_CHOICES)


def test_model_list_record_ignores_the_server_clock():
    # vLLM fills `created` with the current time, so it must not make a resumed run look changed.
    from scripts.docling_benchmark.run import stable_models
    a={'object':'list','data':[{'id':'ornith','created':1,'max_model_len':32768}]}
    b={'object':'list','data':[{'id':'ornith','created':2,'max_model_len':32768}]}
    assert stable_models(a)==stable_models(b)=={'object':'list','data':[{'id':'ornith','max_model_len':32768}]}


def test_image_references_are_not_searchable_text():
    from scripts.docling_benchmark.markdown_tools import strip_images
    md='# Titre\n\n![](images/a1.jpg)\n\nTexte\n\n<div style="text-align: center;"><img src="imgs/x.jpg" alt="Image" width="50%" /></div>\n\nFin'
    assert strip_images(md)=='# Titre\n\nTexte\n\nFin'
    assert strip_images('![](images/only.jpg)')==''
    # Tables and code are kept exactly.
    assert strip_images('<table><tr><td>a</td></tr></table>\n\n```\nx = 1\n```')=='<table><tr><td>a</td></tr></table>\n\n```\nx = 1\n```'


def test_mineru_and_paddle_modules_load_without_their_packages():
    from scripts.docling_benchmark import mineru_adapter, paddle_adapter
    assert mineru_adapter.VARIANTS==('mineru-pipeline','mineru-hybrid')
    assert paddle_adapter.VARIANTS==('paddle-structure','paddle-vl')


def test_meter_passes_health_checks_through(monkeypatch):
    import threading, urllib.request
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from scripts.docling_benchmark import telemetry
    class Up(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'ok')
    up=ThreadingHTTPServer(('127.0.0.1',0),Up);threading.Thread(target=up.serve_forever,daemon=True).start()
    url=f'http://127.0.0.1:{up.server_port}/v1'
    monkeypatch.setattr(telemetry,'ALLOWED_UPSTREAMS',(url,))
    with telemetry.ModelMeter(url) as meter:
        with urllib.request.urlopen(meter.url[:-3]+'/health',timeout=5) as r:assert r.read()==b'ok'
    up.shutdown()


def test_marker_markdown_becomes_one_searchable_segment():
    from scripts.docling_benchmark.marker_adapter import page_result
    p=page_result('## Titre\n\nTexte', 'abc', 8, 'marker-balanced', native='Titre Texte', page_rect=[0,0,10,10])
    assert p.searchable_text=='## Titre\n\nTexte' and p.segments[0].source_text=='Titre Texte'
    assert p.status=='automatic'
    empty=page_result('  ', 'abc', 8, 'marker-balanced', native='Titre', page_rect=[0,0,10,10])
    assert empty.status=='needs_review' and empty.searchable_text==''


def test_marker_llm_calls_use_fixed_generation_settings():
    from scripts.docling_benchmark.marker_adapter import FixedParams
    seen={}
    class Completions:
        def parse(self,**kw):seen.update(kw);return 'r'
    class Chat:completions=Completions()
    class Client:chat=Chat()
    assert FixedParams(Client()).chat.completions.parse(model='ornith',messages=[])=='r'
    assert seen['temperature']==0 and seen['extra_body']=={'chat_template_kwargs':{'enable_thinking':False}}


def _graded_run(tmp_path, page_obj, checks, added=()):
    import json
    from scripts.docling_benchmark.core import bind_review
    folder=tmp_path/'runs'/'x-round1';folder.mkdir(parents=True,exist_ok=True)
    path=folder/'p.page.json';path.write_text(json.dumps(page_obj.dump()))
    path.with_suffix('.review.json').write_text(json.dumps(bind_review(page_obj,checks,reviewer='t',kind='model',added_errors=added)))


def test_problem_on_page_without_any_flag_is_silent(tmp_path):
    from scripts.docling_benchmark.report import generate
    _graded_run(tmp_path,page(),[{'check_id':'a','result':'wrong','evidence':'x'},{'check_id':'b','result':'correct','evidence':'y'}])
    s=generate(tmp_path)['x-round1']
    assert s['problems']==1 and s['silent_problems']==1 and s['flagged_problems']==0


def test_problem_counts_as_flagged_only_when_reviewer_confirms(tmp_path):
    from scripts.docling_benchmark.core import add_review
    from scripts.docling_benchmark.report import generate
    p=page();add_review(p,{'bbox':[0,0,20,20]},'table unclear')
    _graded_run(tmp_path,p,[{'check_id':'a','result':'mixed','evidence':'x','flagged':True},
                            {'check_id':'b','result':'missing','evidence':'y'}],
                added=[{'severity':'major','text':'wrong digit'}])
    s=generate(tmp_path)['x-round1']
    assert s['problems']==3 and s['flagged_problems']==1 and s['silent_problems']==2


def _uml_page():
    d=pymupdf.open();p=d.new_page(width=842,height=595)
    p.insert_text((100,100),'Document',fontsize=12)
    p.insert_text((100,300),'Livre',fontsize=12)
    p.insert_text((400,500),'Conclusion',fontsize=12)
    return d,p


MERMAID='```mermaid\ngraph TD\n  A["Document"] --> B["Livre"]\n```'
UML_MD=('## Titre\n\n![](images/x.jpg)\n\n<details>\n<summary>flowchart</summary>\n\n'+MERMAID+'\n</details>\n\nConclusion')
UML_BLOCKS=[{'type':'text','text':'Titre','bbox':[0,0,1000,50]},
            {'type':'image','content':MERMAID,'sub_type':'flowchart','bbox':[100,100,300,600]},
            {'type':'text','text':'Conclusion','bbox':[450,800,700,900]}]


def test_mineru_review_modules_are_registered():
    from scripts.docling_benchmark import mineru_review
    from scripts.docling_benchmark.run import VARIANT_CHOICES, side_server
    assert mineru_review.VARIANTS==('mineru-text','mineru-ornith','mineru-figures')
    assert set(mineru_review.VARIANTS)<=set(VARIANT_CHOICES)
    # They reuse the stored MinerU output: no MinerU server, only Ornith.
    assert side_server('mineru-text') is None and side_server('mineru-ornith') is None


def test_only_model_made_mineru_blocks_are_selected():
    from scripts.docling_benchmark.mineru_review import model_blocks
    blocks=[{'type':'text','text':'a'},{'type':'list','list_items':['b']},{'type':'header','text':'h'},
            {'type':'code','code_body':'x=1'},{'type':'table','table_body':'<table></table>'},
            {'type':'equation','text':'$$a$$'},{'type':'image','content':'m'},{'type':'image','content':''},
            {'type':'chart','content':'c'}]
    assert [(i,b['type'],c) for i,b,c in model_blocks(blocks)]==[
        (3,'code','x=1'),(4,'table','<table></table>'),(5,'equation','$$a$$'),(6,'image','m'),(8,'chart','c')]


def test_mineru_block_boxes_are_per_mille_of_the_page():
    from scripts.docling_benchmark.mineru_review import block_rect
    assert tuple(block_rect([100,500,1000,1000],pymupdf.Rect(0,0,842,595)))==pytest.approx((84.2,297.5,842,595))
    with pytest.raises(ValueError): block_rect([0,0,1001,10],pymupdf.Rect(0,0,842,595))


def test_picture_block_is_replaced_whole_including_its_wrapper():
    from scripts.docling_benchmark.mineru_review import replace_block
    out=replace_block(UML_MD,UML_BLOCKS[1],MERMAID,'Document\nLivre')
    assert out=='## Titre\n\n![](images/x.jpg)\n\nDocument\nLivre\n\nConclusion'
    assert replace_block('a\n\n$$x$$\n\nb',{'type':'equation'},'$$x$$','')=='a\n\n\n\nb'
    with pytest.raises(ValueError): replace_block('x=1\nx=1',{'type':'code'},'x=1','y')
    with pytest.raises(ValueError): replace_block('nothing',{'type':'code'},'x=1','y')


def test_mineru_text_uses_the_pdf_text_layer_for_pictures():
    from scripts.docling_benchmark.mineru_review import review_page
    d,p=_uml_page()
    row,raw=asyncio.run(review_page('mineru-text',UML_MD,UML_BLOCKS,p,'abc',None))
    assert 'mermaid' not in row.searchable_text and '-->' not in row.searchable_text
    assert 'Document' in row.searchable_text and 'Livre' in row.searchable_text
    assert 'Conclusion' in row.searchable_text and row.reviews==[]
    assert [b['outcome'] for b in raw['blocks']]==['text_layer']


def _ornith(value):
    seen=[]
    async def ask(kind,candidate,native,png):
        seen.append((kind,candidate,native,png[:4]));return value
    return ask,seen


def test_ornith_accept_keeps_the_mineru_block_unchanged():
    from scripts.docling_benchmark.mineru_review import review_page
    d,p=_uml_page();ask,seen=_ornith({'outcome':'accept'})
    row,raw=asyncio.run(review_page('mineru-ornith',UML_MD,UML_BLOCKS,p,'abc',ask))
    assert row.searchable_text=='## Titre\n\n<details>\n<summary>flowchart</summary>\n\n'+MERMAID+'\n</details>\n\nConclusion'
    assert row.reviews==[] and raw['blocks'][0]['outcome']=='accept'
    kind,candidate,native,png=seen[0]
    assert kind=='image' and candidate==MERMAID and 'Document' in native and png==b'\x89PNG'


def test_ornith_replacement_of_a_picture_keeps_the_printed_labels():
    from scripts.docling_benchmark.mineru_review import review_page
    d,p=_uml_page();ask,_=_ornith({'outcome':'replace','content':'Livre hérite de Document.'})
    row,raw=asyncio.run(review_page('mineru-ornith',UML_MD,UML_BLOCKS,p,'abc',ask))
    assert 'Livre hérite de Document.' in row.searchable_text and 'Document\nLivre' in row.searchable_text
    assert '-->' not in row.searchable_text and row.reviews==[]


def test_ornith_review_withholds_the_candidate_and_keeps_native_text():
    from scripts.docling_benchmark.mineru_review import review_page
    d,p=_uml_page();ask,_=_ornith({'outcome':'review','reason':'arrow direction unclear','candidate':'Livre -> Document'})
    row,raw=asyncio.run(review_page('mineru-ornith',UML_MD,UML_BLOCKS,p,'abc',ask))
    assert '-->' not in row.searchable_text and 'Livre -> Document' not in row.searchable_text
    assert 'Document\nLivre' in row.searchable_text
    assert len(row.reviews)==1 and row.reviews[0]['reason']=='arrow direction unclear'
    assert row.reviews[0]['source_ref']['bbox']==pytest.approx([84.2,59.5,252.6,357.0])


def test_ornith_never_rewrites_code_characters():
    from scripts.docling_benchmark.mineru_review import review_page
    d=pymupdf.open();p=d.new_page(width=842,height=595)
    p.insert_text((100,100),'if x:\n    y = 1',fontname='cour',fontsize=10)
    md='Avant\n\n```c\nif x:\ny = 1\n```\n\nApres'
    blocks=[{'type':'code','code_body':'```c\nif x:\ny = 1\n```','bbox':[100,130,400,200]}]
    ask,_=_ornith({'outcome':'replace','content':'if x:\n    y = 2'})
    row,raw=asyncio.run(review_page('mineru-ornith',md,blocks,p,'abc',ask))
    assert 'y = 2' not in row.searchable_text and '    y = 1' in row.searchable_text
    assert len(row.reviews)==1 and 'code' in row.reviews[0]['reason'].lower()


@pytest.mark.parametrize('value',[{'outcome':'review','reason':''},{'outcome':'replace','content':' '},{'outcome':'other'},'bad'])
def test_ornith_bad_answers_become_reviews(value):
    from scripts.docling_benchmark.mineru_review import review_page
    d,p=_uml_page();ask,_=_ornith(value)
    row,raw=asyncio.run(review_page('mineru-ornith',UML_MD,UML_BLOCKS,p,'abc',ask))
    assert '-->' not in row.searchable_text and len(row.reviews)==1
    assert row.reviews[0]['reason_code']=='protocol_failure'


def test_ornith_connection_failure_becomes_a_review():
    from scripts.docling_benchmark.mineru_review import review_page
    d,p=_uml_page()
    async def ask(*a): raise ConnectionError('refused')
    row,raw=asyncio.run(review_page('mineru-ornith',UML_MD,UML_BLOCKS,p,'abc',ask))
    assert '-->' not in row.searchable_text and row.reviews[0]['reason_code']=='model_request_failure'


@pytest.mark.parametrize('content',['graph TD\n  Document --> Livre','```mermaid\nclassDiagram\n  Document <|-- Livre\n```',
                                    'flowchart LR\n  A==>B'])
def test_ornith_figure_replacement_in_diagram_code_is_withheld(content):
    from scripts.docling_benchmark.mineru_review import review_page
    d,p=_uml_page();ask,_=_ornith({'outcome':'replace','content':content})
    row,raw=asyncio.run(review_page('mineru-ornith',UML_MD,UML_BLOCKS,p,'abc',ask))
    assert content not in row.searchable_text and '-->' not in row.searchable_text
    assert len(row.reviews)==1 and row.reviews[0]['candidate_content']==content
    assert 'Document\nLivre' in row.searchable_text


def test_ornith_prompt_names_formatting_and_plain_sentences():
    from scripts.docling_benchmark.mineru_review import REVIEW_PROMPT
    assert 'formatting, not facts' in REVIEW_PROMPT and 'never diagram code' in REVIEW_PROMPT


def test_figure_only_preserves_other_blocks_and_separates_description():
    from scripts.docling_benchmark.mineru_review import review_page
    d,p=_uml_page();ask,seen=_ornith({'outcome':'replace','content':'Livre inherits from Document.'})
    blocks=UML_BLOCKS+[{'type':'code','code_body':'x=1','bbox':[0,0,100,100]}]
    row,raw=asyncio.run(review_page('mineru-figures',UML_MD+'\n\nx=1',blocks,p,'abc',ask))
    assert len(seen)==1 and seen[0][1]==''
    assert 'x=1' in row.searchable_text and 'Livre inherits' in row.searchable_text
    assert raw['blocks'][0]['description']=='Livre inherits from Document.'
    assert 'Document' in raw['blocks'][0]['native']
    assert row.metadata['page_preview']['page_no']==1


@pytest.mark.parametrize('answer',[{'outcome':'accept'}, {'outcome':'replace','content':MERMAID},'invalid'])
def test_figure_only_fallback_preserves_labels_and_reports_issue(answer):
    from scripts.docling_benchmark.mineru_review import review_page
    d,p=_uml_page();ask,seen=_ornith(answer)
    row,raw=asyncio.run(review_page('mineru-figures',UML_MD,UML_BLOCKS,p,'abc',ask))
    assert len(seen)==1 and len(row.reviews)==1
    assert 'Document' in row.searchable_text and 'mermaid' not in row.searchable_text
    assert row.reviews[0]['source_ref']['page_no']==1


def test_figure_only_visits_detected_picture_without_candidate():
    from scripts.docling_benchmark.mineru_review import review_page
    d,p=_uml_page();ask,seen=_ornith({'outcome':'replace','content':'Two classes are connected.'})
    block=dict(UML_BLOCKS[1],content='')
    row,raw=asyncio.run(review_page('mineru-figures','Titre',[block],p,'abc',ask))
    assert len(seen)==1 and 'Two classes' in row.searchable_text
    assert 'Document' in row.searchable_text


def test_figure_only_connection_failure_keeps_source():
    from scripts.docling_benchmark.mineru_review import review_page
    d,p=_uml_page()
    async def fail(*args):raise ConnectionError('offline')
    row,raw=asyncio.run(review_page('mineru-figures',UML_MD,UML_BLOCKS,p,'abc',fail))
    assert row.reviews[0]['reason_code']=='model_request_failure'
    assert 'Document' in row.searchable_text


def test_second_step_reuses_source_timing_requests_and_hash(tmp_path):
    import json, hashlib
    from scripts.docling_benchmark.mineru_review import MinerUReviewAdapter
    d,p=_uml_page();pdf=tmp_path/'source.pdf';d.save(pdf)
    digest=hashlib.sha256(pdf.read_bytes()).hexdigest()
    folder=tmp_path/'mineru-hybrid-round1';folder.mkdir()
    (folder/'config.json').write_text('{}')
    from scripts.docling_benchmark.markdown_tools import strip_images
    source={'file_sha256':digest,'page_no':1,'searchable_text':strip_images(UML_MD),
            'metadata':{'requests':[{'server':'mineru-vlm','usage':{'total_tokens':123}}]},'seconds':2.5}
    (folder/'x.page.json').write_text(json.dumps(source))
    raw=json.dumps({'markdown':UML_MD,'content_list':UML_BLOCKS})
    (folder/'x.raw.json').write_text(raw)
    adapter=MinerUReviewAdapter('mineru-text',folder,'unused','unused')
    row,out=asyncio.run(adapter.read(pdf,1,digest))
    assert row.metadata['source_seconds']==2.5
    assert row.metadata['source_requests']==source['metadata']['requests']
    assert out['source']['raw_sha256']==hashlib.sha256(raw.encode()).hexdigest()
    assert row.metadata['picture_regions'][0]['bbox']


def test_resume_ignores_only_ephemeral_model_permissions(tmp_path):
    import json
    from scripts.pdf_ingest_resume_run import checked_freeze
    old={'model':'ornith','code':{'worker.py':'fixed'},'models':{'data':[{'id':'ornith','max_model_len':32768,
        'permission':[{'id':'one','created':1,'allow_sampling':True}]}]}}
    tmp_path.joinpath('config.json').write_text(json.dumps(old))
    new=json.loads(json.dumps(old));new['models']['data'][0]['permission'][0].update(id='two',created=2)
    checked_freeze(tmp_path,new)
    assert json.loads(tmp_path.joinpath('config.json').read_text())==old
    new['code']['worker.py']='changed'
    with pytest.raises(ValueError,match='code'):
        checked_freeze(tmp_path,new)
