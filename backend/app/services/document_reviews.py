"""Review persistence; callers own the transaction and course authorization."""
import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from sqlalchemy import select
from backend.app.models import Document, DocumentReview

CONFLICT_CODES = ('review_revision_conflict', 'review_source_conflict', 'review_source_changed')


class ReviewConflict(ValueError):
    """A source or revision no longer matches the teacher's view."""

    def __init__(self, message, code='review_revision_conflict'):
        if code not in CONFLICT_CODES:
            raise ValueError(f'Unknown review conflict code: {code}')
        super().__init__(message)
        self.code = code


def _prepare(doc, item):
    if item['file_sha256'] != doc.content_hash:
        raise ReviewConflict('Source file changed; upload a new document', 'review_source_changed')
    n=item['page_no']
    if isinstance(n,bool) or not isinstance(n,int) or n<1 or (doc.page_count and n>doc.page_count):
        raise ValueError('Invalid physical page')
    box=item['bbox']
    if (not isinstance(box,(list,tuple)) or len(box)!=4 or
        any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in box) or
        not (0<=box[0]<box[2] and 0<=box[1]<box[3])):
        raise ValueError('Invalid review bbox')
    ids=item['ids']; regions=item['region_ids']
    for values in (ids,regions):
        if not isinstance(values,(list,tuple)) or any(not isinstance(v,str) or not v for v in values) or len(values)!=len(set(values)):
            raise ValueError('Invalid source identifiers')
    reason=item['reason']
    if not isinstance(reason,str) or not 1<=len(reason.strip())<=2000:
        raise ValueError('Review needs a concrete reason')
    candidate=item.get('candidate_content')
    if candidate is not None and (not isinstance(candidate,str) or len(candidate)>10000):
        raise ValueError('Invalid candidate content')
    ref=item.get('source_ref')
    if ref is not None:
        # Built by the shared source function, never by the model; it must describe this record.
        from backend.worker.visual_sources import source_group_key
        try:
            valid=(ref['version']==3 and ref['file_sha256']==doc.content_hash and ref['page_no']==n
                   and set(ids)<=set(ref['ids']) and {r['id'] for r in ref['regions']}<=set(regions))
            key=source_group_key(ref)
        except (KeyError,TypeError,ValueError):
            valid=False
        if not valid:
            raise ValueError('Review source does not match its record')
    else:
        # Legacy report: only original native/candidate IDs from this exact PDF. Never crop identity.
        ref={'version':1,'file_sha256':doc.content_hash,'page_no':n,
             'ids':sorted(ids),'regions':sorted(regions)}
        key=hashlib.sha256(json.dumps(ref,sort_keys=True).encode()).hexdigest()
    return key,ref,list(box),reason.strip(),candidate


def _review_overlap(ref, rows):
    """A new source must repeat an existing review exactly or be clearly separate."""
    from backend.worker.visual_sources import compare_sources
    return any((row.status=='pending' or row.source_ref.get('scope')!='page')
               and compare_sources(ref,row.source_ref) in ('overlap','uncertain') for row in rows)


async def register_reviews(db, doc, items):
    prepared=[_prepare(doc,item) for item in items]
    if len({p[0] for p in prepared})!=len(prepared):
        raise ValueError('Duplicate review source in one run')
    await db.scalar(select(Document).where(Document.id==doc.id).with_for_update())
    existing={r.source_key:r for r in (await db.scalars(select(DocumentReview)
        .where(DocumentReview.document_id==doc.id).with_for_update()
        .execution_options(populate_existing=True))).all()}
    if any(row.file_sha256 != doc.content_hash for row in existing.values()):
        raise ReviewConflict('Source file changed; upload a new document', 'review_source_changed')
    if any(key not in existing and _review_overlap(ref,existing.values()) for key,ref,*_ in prepared):
        raise ReviewConflict('Review overlaps an existing review; the old review stays the task', 'review_source_conflict')
    rows=[]
    for key,ref,box,reason,candidate in prepared:
        row=existing.get(key)
        if row is None:
            row=DocumentReview(document_id=doc.id,file_sha256=doc.content_hash,source_key=key,
                source_ref=ref,page_no=ref['page_no'],bbox=box,reason=reason,
                candidate_content=candidate,status='pending',revision=1)
            db.add(row)
        elif row.status=='pending' and (row.bbox!=box or row.reason!=reason or row.candidate_content!=candidate):
            row.bbox=box;row.reason=reason;row.candidate_content=candidate;row.revision+=1
        rows.append(row)
    await db.flush()
    return rows


@dataclass(frozen=True)
class PreparedConfirmation:
    final_content: str
    texts: tuple[str, ...]
    refs: tuple[dict, ...]
    source_text: str
    replaced: tuple            # chunk ids of the one source group, in part order
    start: int                 # chunk_index of the first part
    page_snapshot: tuple       # the page as seen while preparing


def _page_snapshot(chunks):
    return tuple(sorted((str(c.id), c.content, c.edited_by_teacher, c.source_fingerprint) for c in chunks))


def _native_copy(doc, ref):
    """Program copy of the review's native lines, read again from the verified original PDF."""
    from pathlib import Path
    import pymupdf
    from backend.worker.page_reader import _extract_native_page, _native_group_text
    try:
        data=Path(doc.storage_path).read_bytes()
    except OSError:
        raise ReviewConflict('Original file unavailable; existing content was not changed', 'review_source_changed') from None
    if hashlib.sha256(data).hexdigest()!=ref['file_sha256']:
        raise ReviewConflict('Original file changed; existing content was not changed', 'review_source_changed')
    with pymupdf.open(stream=data,filetype='pdf') as pdf:
        if not 1<=ref['page_no']<=len(pdf):
            raise ReviewConflict('Original physical page unavailable', 'review_source_changed')
        page=_extract_native_page(pdf[ref['page_no']-1],ref['page_no'])
    lines={l.id:l for l in page.lines}
    if any(i not in lines or start!=0 or end!=len(lines[i].text) for i,start,end in ref['source_ranges']):
        raise ReviewConflict('Native source no longer matches this review; existing content was not changed', 'review_source_changed')
    selected=[l for l in page.lines if l.id in set(ref['ids'])]
    return _native_group_text(selected,'code'),'\n'.join(l.text for l in selected)


def prepare_review_confirmation(doc, review, chunks, final_content) -> PreparedConfirmation:
    """Match the review to one complete indexed source and compose the final text.

    The teacher confirms the visual explanation only; native text (code included)
    is copied by the program, never retyped. Pure pictures keep only the
    explanation. A page-level review adds a separate page note. No lock is held.
    """
    from backend.worker.chunking import MAX_CHUNK_CHARS, _split_text
    from backend.worker.visual_sources import compare_sources, source_group_key
    if not isinstance(final_content,str) or not 1<=len(final_content.strip())<=20000:
        raise ValueError('Provide final content, at most 20000 characters')
    if review.file_sha256!=doc.content_hash:
        raise ReviewConflict('Source file changed', 'review_source_changed')
    ref=review.source_ref
    page_chunks=[c for c in chunks if c.page_no==review.page_no]
    page_scope=ref.get('scope')=='page'
    matched=[]
    for chunk in page_chunks:
        state=compare_sources(chunk.source_ref,ref)
        if state=='exact':
            matched.append(chunk)
        elif not page_scope and state!='disjoint' and (chunk.source_ref or {}).get('scope')!='page':
            raise ReviewConflict('Source overlaps differently grouped content; existing content was not changed',
                                 'review_source_conflict')
    matched.sort(key=lambda c:(c.source_ref.get('part',0),c.chunk_index))
    if matched:
        parts=[c.source_ref.get('part') for c in matched]
        indexes=[c.chunk_index for c in matched]
        if (parts!=list(range(len(matched))) or {c.source_ref.get('parts') for c in matched}!={len(matched)}
                or indexes!=list(range(indexes[0],indexes[0]+len(matched)))):
            raise ReviewConflict('Indexed parts of this source are incomplete; existing content was not changed',
                                 'review_source_conflict')
        if any(c.edited_by_teacher for c in matched):
            raise ReviewConflict('Teacher-edited content cannot be merged safely; existing content was not changed',
                                 'review_source_conflict')
    native=''
    source_text=matched[0].source_text or '' if matched else ''
    if ref.get('ids') and not page_scope:
        if ref.get('version')!=3:
            raise ReviewConflict('Legacy review source cannot be proven; existing content was not changed',
                                 'review_source_conflict')
        if not matched:
            raise ReviewConflict('Native source not indexed; process this page before confirming',
                                 'review_source_conflict')
        native,source_text=_native_copy(doc,ref)
        if matched[0].source_text!=source_text:
            raise ReviewConflict('Indexed native text differs from the original file', 'review_source_changed')
    teacher=final_content.strip()
    content=teacher+('\n'+native if native else '')
    texts=tuple(_split_text(content,MAX_CHUNK_CHARS,code=True))
    base={k:v for k,v in (matched[0].source_ref if matched else ref).items()
          if k not in ('part','parts','previous_part','next_part','split_signature','group_id')}
    group=source_group_key(base) if base.get('version')==3 else None
    refs=[]
    for i in range(len(texts)):
        part={**base,'part':i,'parts':len(texts),'previous_part':i-1 if i else None,
              'next_part':i+1 if i+1<len(texts) else None}
        if group:part['group_id']=group
        if len(texts)>1:part['split_signature']=hashlib.sha256(content.encode()).hexdigest()
        refs.append(part)
    start=matched[0].chunk_index if matched else max((c.chunk_index for c in page_chunks),default=-1)+1
    return PreparedConfirmation(teacher,texts,tuple(refs),source_text,tuple(c.id for c in matched),start,
                                _page_snapshot(page_chunks))


async def _page_chunks(db, doc, page_no, *, lock):
    from backend.app.models import DocChunk
    query=select(DocChunk).where(DocChunk.document_id==doc.id,DocChunk.page_no==page_no)
    if lock:
        query=query.with_for_update().execution_options(populate_existing=True)
    return list((await db.scalars(query)).all())


async def publish_review_confirmation(db, doc, review_id, prepared, embeddings, *, revision, status, teacher_id):
    """Lock Document -> DocumentReview -> DocChunk, re-check the snapshot, write once."""
    from datetime import datetime, timezone
    from sqlalchemy import delete
    from backend.app.models import DocChunk, DocumentStatus
    from backend.worker.chunking import Chunk
    from backend.worker.ingest import _source_fingerprint, _tsv_value
    doc=await db.scalar(select(Document).where(Document.id==doc.id).with_for_update()
                        .execution_options(populate_existing=True))
    row=await db.scalar(select(DocumentReview).where(DocumentReview.id==review_id,
        DocumentReview.document_id==doc.id).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise LookupError('Review not found')
    if row.revision!=revision or row.status!='pending':
        raise ReviewConflict('Review changed; reload before submitting')
    if row.file_sha256!=doc.content_hash:
        raise ReviewConflict('Source file changed', 'review_source_changed')
    current=await _page_chunks(db,doc,row.page_no,lock=True)
    if prepared is not None:
        if _page_snapshot(current)!=prepared.page_snapshot:
            raise ReviewConflict('Page content changed while preparing; reload before submitting')
        # Later chunks of this page move so the new parts stay one contiguous run.
        shift=len(prepared.texts)-len(prepared.replaced)
        for chunk in current:
            if chunk.id not in prepared.replaced and chunk.chunk_index>=prepared.start+len(prepared.replaced):
                chunk.chunk_index+=shift
        if prepared.replaced:
            await db.execute(delete(DocChunk).where(DocChunk.id.in_(prepared.replaced)))
        for index,(text,ref,vector) in enumerate(zip(prepared.texts,prepared.refs,embeddings)):
            value=Chunk(text,row.page_no,None,source_text=prepared.source_text,source_ref=ref)
            db.add(DocChunk(id=prepared.replaced[index] if index<len(prepared.replaced) else uuid.uuid4(),
                document_id=doc.id,class_id=doc.class_id,lab_id=doc.lab_id,chunk_index=prepared.start+index,
                content=text,source_text=prepared.source_text,source_ref=ref,source_fingerprint=_source_fingerprint(value),
                embedding=vector,tsv=_tsv_value(db,text,doc.language),page_no=row.page_no,edited_by_teacher=True))
        row.final_content=prepared.final_content
    row.status=status;row.revision+=1;row.resolved_by=teacher_id;row.resolved_at=datetime.now(timezone.utc)
    await db.flush()
    chunks=list((await db.scalars(select(DocChunk).where(DocChunk.document_id==doc.id)
        .order_by(DocChunk.page_no,DocChunk.chunk_index,DocChunk.id))).all())
    for index,chunk in enumerate(chunks):chunk.chunk_index=index
    all_reviews=list((await db.scalars(select(DocumentReview).where(DocumentReview.document_id==doc.id))).all())
    pending=sum(r.status=='pending' for r in all_reviews)
    available={c.page_no for c in chunks}
    missing=set(range(1,(doc.page_count or 0)+1))-available
    unresolved_pages=any(p.get('status') not in ('read','updated','partial') for p in
        (doc.ingest_report or {}).get('page_reader',{}).get('page_results',[]))
    if chunks:
        doc.status=DocumentStatus.partially_indexed if pending or missing or unresolved_pages or not doc.page_count else DocumentStatus.indexed
    else:
        doc.status=DocumentStatus.needs_review
    doc.ingest_report={**(doc.ingest_report or {}),'review_summary':{'pending':pending,
        'confirmed':sum(r.status=='confirmed' for r in all_reviews),'ignored':sum(r.status=='ignored' for r in all_reviews)}}
    doc.error_message=None if doc.status==DocumentStatus.indexed else f'{pending} review tasks pending; missing pages {sorted(missing)}'
    await db.commit()
    return row


async def resolve_review(db, doc, review_id, *, revision, status, content, teacher_id, embed_fn):
    """Prepare and embed without a lock, then publish atomically; never guess ownership."""
    from backend.worker.ingest import _embed_batches
    if status not in ('confirmed','ignored'):
        raise ValueError('Choose confirmed or ignored')
    if status=='confirmed' and (not isinstance(content,str) or not 1<=len(content.strip())<=20000):
        raise ValueError('Provide final content, at most 20000 characters')
    row=await db.scalar(select(DocumentReview).where(DocumentReview.id==review_id,
        DocumentReview.document_id==doc.id))
    if row is None:
        raise LookupError('Review not found')
    if row.revision!=revision or row.status!='pending':
        raise ReviewConflict('Review changed; reload before submitting')
    if row.file_sha256!=doc.content_hash:
        raise ReviewConflict('Source file changed', 'review_source_changed')
    prepared=embeddings=None
    if status=='confirmed':
        prepared=prepare_review_confirmation(doc,row,await _page_chunks(db,doc,row.page_no,lock=False),content)
        embeddings=await _embed_batches(list(prepared.texts),embed_fn)
    return await publish_review_confirmation(db,doc,review_id,prepared,embeddings,
                                             revision=revision,status=status,teacher_id=teacher_id)


def render_review_image(doc, row):
    from pathlib import Path
    import pymupdf
    if hashlib.sha256(Path(doc.storage_path).read_bytes()).hexdigest()!=row.file_sha256:
        raise ReviewConflict('Original file changed; screenshot unavailable', 'review_source_changed')
    with pymupdf.open(doc.storage_path) as pdf:
        if not 1<=row.page_no<=len(pdf):
            raise ReviewConflict('Original physical page unavailable', 'review_source_changed')
        page=pdf[row.page_no-1]
        box=pymupdf.Rect(row.bbox)
        if box.is_empty or not page.rect.contains(box):
            raise ReviewConflict('Registered region is outside the original page', 'review_source_changed')
        scale=min(4,2000/max(box.width,box.height))
        return page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),clip=box,alpha=False).tobytes('png')
