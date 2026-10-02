"""Offline adapters. No application database access."""
import ast
import base64
import json
import statistics
import time

import pymupdf

from .core import PageResult, Segment, add_review, picture_result
from .native import checked_box, check_generated, rebuild
from .suite import HISTORY

PICTURE_PROMPT = '''Describe only visible information. Nearby native text is context,
not permission to rewrite it. Return one JSON object:
{"outcome":"supplement","content":"brief description"} or
{"outcome":"review","reason":"specific unresolved detail","candidate":"optional draft"}.
For unclear text, relationships, cropped content, or image code choose review.
Do not infer unseen arrows or facts. Formulas may use LaTeX and tables Markdown.
No confidence score or reasoning text. Never transcribe or correct native code.'''


def legacy_functions():
    tree = ast.parse((HISTORY / 'native_code.py').read_text())
    funcs = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef)], type_ignores=[])
    ns = {'pymupdf': pymupdf, 'statistics': statistics}
    exec(compile(funcs, str(HISTORY / 'native_code.py'), 'exec'), ns)
    return ns['rebuild']


def legacy_prompt():
    tree = ast.parse((HISTORY / 'run_docling_tuned.py').read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'PROMPT' for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('Legacy prompt missing')


def source_for(item, page, digest):
    refs = []
    for prov in item.prov:
        if prov.page_no != page.number + 1:
            continue
        b = prov.bbox
        if str(b.coord_origin).lower().endswith('bottomleft'):
            box = [b.l, page.rect.height-b.t, b.r, page.rect.height-b.b]
        else:
            box = [b.l, b.t, b.r, b.b]
        checked_box(page, box)
        refs.append(box)
    if len(refs) != 1:
        raise ValueError('Expected one source box on the matching physical page')
    return {'version': 1, 'file_sha256': digest, 'page_no': page.number+1,
            'bbox': refs[0], 'coord_origin': 'TOPLEFT', 'item_ref': item.self_ref}


def overlapping(box, prior):
    r = pymupdf.Rect(box)
    return any((r & pymupdf.Rect(p)).get_area() > 0.2 * min(r.get_area(), pymupdf.Rect(p).get_area()) for p in prior)


class DoclingAdapter:
    def __init__(self, variant, base_url, model):
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, PictureDescriptionApiOptions
        from docling.datamodel.accelerator_options import AcceleratorOptions, AcceleratorDevice
        self.variant, self.model, self.base_url = variant, model, base_url
        self.legacy = variant == 'legacy-c-api'
        options = PdfPipelineOptions(do_formula_enrichment=variant != 'patched-no-formula',
            do_code_enrichment=self.legacy,
            accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CUDA, num_threads=8),
            document_timeout=240)
        if self.legacy:
            options.do_picture_description = True
            options.generate_picture_images = True
            options.images_scale = 2
            options.enable_remote_services = True
            options.picture_description_options = PictureDescriptionApiOptions(
                url=base_url+'/chat/completions', prompt=legacy_prompt(), timeout=180,
                params={'model':model, 'max_tokens':2048, 'temperature':0,
                        'chat_template_kwargs':{'enable_thinking':False}})
        self.options = json.loads(options.model_dump_json())
        self.converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
        started = time.monotonic()
        self.converter.initialize_pipeline(InputFormat.PDF)
        self.initialization_seconds = time.monotonic()-started
        self.legacy_rebuild = legacy_functions() if self.legacy else None

    async def read(self, path, page_no, digest):
        result = PageResult(digest, page_no, self.variant)
        start = time.monotonic()
        converted = self.converter.convert(path, page_range=(page_no,page_no), raises_on_error=False)
        doc = converted.document
        raw_doc = doc.export_to_dict()
        original_export = doc.export_to_markdown()
        result.metadata['conversion_status'] = str(converted.status)
        result.errors.extend({'kind':'conversion', 'message':str(e)} for e in converted.errors)
        with pymupdf.open(path) as pdf:
            page = pdf[page_no-1]
            native = page.get_text()
            if self.legacy:
                for t in doc.texts:
                    if str(t.label) != 'code':
                        continue
                    recovered = self.legacy_rebuild(page, t.prov[0].bbox.model_dump())
                    if recovered:
                        t.text = t.orig = recovered
                result.segments.append(Segment(doc.export_to_markdown(), native,
                    {'file_sha256':digest,'page_no':page_no,'bbox':list(page.rect)}, original_export))
                result.metadata['item_sources'] = [t.model_dump(mode='json').get('prov') for t in doc.texts]
            else:
                used_boxes = []
                for item, _ in doc.iterate_items(traverse_pictures=True):
                    label = str(item.label)
                    if label == 'picture':
                        continue
                    try:
                        source = source_for(item,page,digest)
                    except ValueError as exc:
                        add_review(result, {'page_no':page_no,'item_ref':item.self_ref}, str(exc), 'source_failure')
                        continue
                    original = page.get_textbox(pymupdf.Rect(source['bbox']))
                    extracted = getattr(item, 'text', '')
                    if label == 'table':
                        extracted = item.export_to_markdown(doc=doc)
                    final = extracted
                    issue = check_generated(extracted)
                    if not extracted.strip() and original.strip():
                        issue = 'Empty extraction despite available native text'
                    if label == 'code':
                        if overlapping(source['bbox'],used_boxes):
                            issue, final = 'Overlapping code source boxes', original
                        else:
                            recovered = rebuild(page, source['bbox'])
                            final = recovered.text or original
                            issue = '; '.join(recovered.issues) or None
                        used_boxes.append(source['bbox'])
                    if issue:
                        final = original
                        add_review(result,source,issue,candidate=extracted)
                    if final.strip():
                        result.segments.append(Segment(final,original,source,extracted,label))
                await self.pictures(result,doc,page,native,used_boxes)
            if not result.segments and not result.reviews:
                add_review(result,{'page_no':page_no},'No searchable content extracted')
        result.seconds = time.monotonic()-start
        return result, raw_doc

    async def pictures(self,result,doc,page,native,code_boxes):
        from openai import AsyncOpenAI, APIConnectionError, APIStatusError
        async with AsyncOpenAI(base_url=self.base_url,api_key='local',timeout=120,max_retries=0) as client:
            seen = set()
            for item in doc.pictures:
                try:
                    source = source_for(item,page,result.file_sha256)
                except ValueError as exc:
                    add_review(result,{'page_no':result.page_no,'item_ref':item.self_ref},str(exc),'source_failure')
                    continue
                key = tuple(source['bbox'])
                if key in seen:
                    result.metadata.setdefault('duplicate_picture_refs',[]).append(item.self_ref)
                    continue
                seen.add(key)
                r = pymupdf.Rect(key)
                if not native.strip():
                    add_review(result,source,'Image-only or scanned page requires review');continue
                if any(r.intersects(pymupdf.Rect(b)) for b in code_boxes):
                    add_review(result,source,'Picture overlaps native code; review visual information');continue
                scale = min(2.0,1600/max(r.width,r.height))
                png = page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),clip=r).tobytes('png')
                nearby = [dict(text=s.source_text,source_ref=s.source_ref) for s in result.segments
                    if s.source_text and 'bbox' in s.source_ref and
                    (r+(-24,-24,24,24)).intersects(pymupdf.Rect(s.source_ref['bbox']))]
                async def request():
                    try:
                        response = await client.chat.completions.create(model=self.model,temperature=0,max_tokens=2048,
                            extra_body={'chat_template_kwargs':{'enable_thinking':False}},
                            response_format={'type':'json_object'}, messages=[{'role':'user','content':[
                                {'type':'text','text':PICTURE_PROMPT+'\nNearby native context: '+json.dumps(nearby,ensure_ascii=False)},
                                {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(png).decode()}}]}])
                    except (APIConnectionError, APIStatusError) as exc:
                        raise ConnectionError(str(exc)) from exc
                    if response.usage:
                        result.usage.append(response.usage.model_dump())
                    if response.choices[0].finish_reason != 'stop':
                        return {'outcome':'invalid'}
                    try:
                        return json.loads(response.choices[0].message.content)
                    except (ValueError,TypeError):
                        return {'outcome':'invalid'}
                await picture_result(result,source,request)


class WorkerAdapter:
    initialization_seconds = 0
    options = {'max_calls_per_page':6,'timeout':120,'temperature':0,'max_tokens':8192}

    def __init__(self,base_url,model):
        self.base_url,self.model=base_url,model

    async def read(self,path,page_no,digest):
        from openai import AsyncOpenAI
        from backend.worker.region_reader import make_region_reader
        from backend.worker.page_reader import chunk_read_pages, extract_native_pages
        row=PageResult(digest,page_no,'worker-v9.1')
        start=time.monotonic()
        prepared=extract_native_pages(str(path),geometry_hints=True,isolate_errors=True)
        preparation=time.monotonic()-start
        async with AsyncOpenAI(base_url=self.base_url,api_key='local',timeout=120,max_retries=0) as client:
            pages,audit=await make_region_reader(client,self.model)(str(path),prepared_pages=prepared,page_numbers={page_no})
        for c in chunk_read_pages(pages,file_sha256=digest):
            row.segments.append(Segment(c.content,c.source_text or '',c.source_ref or {}))
        row.reviews=audit.get('reviews',[])
        row.calls=sum(p.calls for p in pages)
        for outcome in audit['page_results']:
            if outcome.get('reason'):
                row.errors.append({'kind':outcome['reason'],'message':outcome.get('error','')})
        row.seconds=time.monotonic()-start
        row.metadata['full_document_preparation_seconds']=preparation
        row.metadata['preparation_policy']='full geometry preparation per selected page; reported separately'
        return row,audit
