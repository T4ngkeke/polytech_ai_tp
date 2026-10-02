"""PDF to the M2 document IR. MinerU is an injectable extractor, not the IR owner."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Callable

import pymupdf

from backend.worker.document_ir import (ParsedAsset, ParsedBlock, ParsedDocument,
    ParsedPage, ParseIssue, SourceRef, stable_id)

IDENTITY_VERSION = 'pdf-block-v1'
READER_VERSION = 'mineru-text-m3a-1'
CONFIG = {'backend': 'hybrid-http-client', 'parse_method': 'auto', 'formula_enable': True,
          'table_enable': True, 'lang': 'ch', 'effort': 'high', 'image_analysis': True,
          'figure_text': 'native_pdf_region'}
CONFIG_HASH = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()


class PDFReadError(ValueError):
    pass


def _rect(value, page):
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        return None
    try:
        box = tuple(float(x) for x in value)
    except (TypeError, ValueError):
        return None
    if not (0 <= box[0] < box[2] <= 1000 and 0 <= box[1] < box[3] <= 1000):
        return None
    rect = pymupdf.Rect(page.rect.x0 + box[0] * page.rect.width / 1000,
                        page.rect.y0 + box[1] * page.rect.height / 1000,
                        page.rect.x0 + box[2] * page.rect.width / 1000,
                        page.rect.y0 + box[3] * page.rect.height / 1000)
    return rect if page.rect.contains(rect) else None


def _content(item, page, rect):
    kind = item.get('type')
    if kind in ('image', 'chart'):
        return ('figure', page.get_text(clip=rect).strip() if rect else '', 'plain')
    if kind == 'code':
        return ('code', item.get('code_body'), 'markdown')
    if kind == 'table':
        return ('table', item.get('table_body'), 'markdown')
    if kind == 'equation':
        return ('formula', item.get('text'), 'latex' if item.get('text_format') == 'latex' else 'plain')
    if kind == 'list':
        value = item.get('list_items')
        if isinstance(value, list): value = '\n'.join(str(v) for v in value)
        return ('text', value, 'plain')
    if kind in ('header', 'footer', 'page_number'):
        return ('navigation', item.get('text'), 'plain')
    if kind == 'text':
        return ('heading' if item.get('text_level') else 'text', item.get('text'), 'plain')
    return (None, None, None)


def read_pdf(path: Path, expected_sha256: str, work_dir: Path,
             extract_page: Callable[[Path, int, Path], list[dict]], *, max_pages: int = 150, budget_seconds: int | None = None,
             preview_scale: float = 1.2, reader_identity: str = IDENTITY_VERSION) -> tuple[ParsedDocument, dict]:
    path, work_dir = Path(path), Path(work_dir)
    hasher=hashlib.sha256()
    with path.open('rb') as handle:
        if handle.read(5) != b'%PDF-':
            raise PDFReadError('invalid_pdf')
        handle.seek(0)
        for chunk in iter(lambda: handle.read(1024*1024), b''):
            hasher.update(chunk)
    digest=hasher.hexdigest()
    if digest != expected_sha256:
        raise PDFReadError('source_hash_mismatch')
    try:
        pdf = pymupdf.open(path)
    except Exception as exc:
        raise PDFReadError('unreadable_pdf') from exc
    with pdf:
        if pdf.needs_pass or not 0 < len(pdf) <= max_pages:
            raise PDFReadError('pdf_password_or_page_limit')
        work_dir.mkdir(parents=True, exist_ok=True)
        doc = ParsedDocument(source_sha256=digest, source_filename=path.name,
                             source_size_bytes=path.stat().st_size, reader_version=READER_VERSION,
                             identity_version=reader_identity, config_hash=CONFIG_HASH,
                             source_slide_count=len(pdf))
        doc.assets.append(ParsedAsset(asset_id=stable_id(reader_identity,digest,'pdf'),
            kind='preview_pdf', mime='application/pdf', sha256=digest,
            size_bytes=path.stat().st_size, source_entry=str(path), access_class='student_visible'))
        timing=[]
        deadline=time.monotonic()+budget_seconds if budget_seconds is not None else None
        for page_no in range(1, len(pdf)+1):
            page = pdf[page_no-1]
            page_id = stable_id(reader_identity,digest,'page',str(page_no))
            preview = work_dir / f'page-{page_no}.png'
            page.get_pixmap(matrix=pymupdf.Matrix(preview_scale,preview_scale),alpha=False).save(preview)
            data=preview.read_bytes()
            preview_id=stable_id(reader_identity,digest,'preview',str(page_no))
            doc.assets.append(ParsedAsset(asset_id=preview_id,kind='page_preview',mime='image/png',
                sha256=hashlib.sha256(data).hexdigest(),size_bytes=len(data),source_entry=str(preview),
                page_id=page_id,access_class='student_visible'))
            parsed_page=ParsedPage(page_id=page_id,order_index=page_no-1,source_slide_no=page_no,
                preview_page_no=page_no,preview_mapping_check='same_pdf_page',preview_asset_id=preview_id,
                width_cm=page.rect.width*2.54/72,height_cm=page.rect.height*2.54/72)
            started=time.monotonic()
            try:
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError('pdf_job_budget_exhausted')
                items=extract_page(path,page_no,work_dir / f'mineru-{page_no}')
                if not isinstance(items,list): raise ValueError('content_list_not_array')
            except Exception as exc:
                parsed_page.state='partial'
                parsed_page.issues.append(ParseIssue(code='budget_exhausted' if str(exc)=='pdf_job_budget_exhausted' else 'extraction_failure',
                    scope='page',page_id=page_id,message='MinerU page extraction failed',
                    detail={'error_type':type(exc).__name__}))
                items=[]
            timing.append({'page_no':page_no,'seconds':round(time.monotonic()-started,3),
                           'status':'ok' if parsed_page.state=='usable' else 'failed',
                           'model_calls':None,'model_usage':None})
            for index,item in enumerate(items):
                if not isinstance(item,dict) or item.get('page_idx') not in (0,'0',None):
                    parsed_page.state='partial'
                    parsed_page.issues.append(ParseIssue(code='source_identity_conflict',scope='page',page_id=page_id,
                        message='MinerU content_list page identity invalid',detail={'index':index}))
                    continue
                rect=_rect(item.get('bbox'),page)
                kind,content,fmt=_content(item,page,rect)
                if kind is None:
                    parsed_page.state='partial'
                    parsed_page.issues.append(ParseIssue(code='content_review',scope='page',page_id=page_id,
                        message='Unknown MinerU block type',detail={'index':index,'type':str(item.get('type'))[:80]}))
                    continue
                block_id=stable_id(reader_identity,digest,'block',str(page_no),str(index))
                block=ParsedBlock(block_id=block_id,page_id=page_id,order_index=len(parsed_page.blocks),
                    kind=kind,content=content if isinstance(content,str) else '',content_format=fmt,
                    source_ref=SourceRef(file_sha256=digest,source_slide_no=page_no,format='pdf',
                        extraction_method='mineru_hybrid_native_figure_labels' if kind=='figure' else 'mineru_hybrid',
                        identity_version=reader_identity,bbox=None,bbox_basis='unknown',
                        content_origin='pdf_text_layer_region' if kind=='figure' else
                                       'mineru_model' if kind in ('code','table','formula') else 'pdf_text_layer',
                        legacy_ref={'mineru_bbox_per_mille':item.get('bbox'),'block_index':index}),
                    source_completeness='unknown' if kind in ('figure','formula','table','code') or not content else 'complete',
                    preview_required=kind=='figure',preview_scope='page',
                    source_text=content if kind in ('figure','heading','text','navigation') and isinstance(content,str) else None,
                    model_candidate=item.get('content') if kind=='figure' and isinstance(item.get('content'),str) else None)
                if kind=='figure':
                    block.asset_ids=(preview_id,)
                    if not content:
                        block.issues.append(ParseIssue(code='content_review',scope='block',page_id=page_id,
                            block_id=block_id,message='Figure has no native PDF labels; use page preview'))
                if rect is None and item.get('bbox') is not None:
                    block.issues.append(ParseIssue(code='source_geometry_unverified',scope='block',page_id=page_id,
                        block_id=block_id,message='MinerU region cannot be mapped to PDF page'))
                if not isinstance(content,str):
                    block.issues.append(ParseIssue(code='content_review',scope='block',page_id=page_id,
                        block_id=block_id,message='MinerU block content missing or invalid'))
                parsed_page.blocks.append(block)
            if not parsed_page.blocks and parsed_page.state=='usable':
                parsed_page.state='partial'
                parsed_page.issues.append(ParseIssue(code='extraction_failure',scope='page',page_id=page_id,
                    message='MinerU returned no blocks for this page'))
            doc.pages.append(parsed_page)
        return doc.validate(), {'extractor':READER_VERSION,'config_hash':CONFIG_HASH,'pages':timing,
                                'model_calls':None,'model_usage':None}
