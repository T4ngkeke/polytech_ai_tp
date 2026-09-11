"""Review persistence; callers own the transaction and course authorization."""
import hashlib
import json
import math
from sqlalchemy import select
from backend.app.models import Document, DocumentReview


class ReviewConflict(ValueError):
    """A source or revision no longer matches the teacher's view."""


def _prepare(doc, item):
    if item['file_sha256'] != doc.content_hash:
        raise ReviewConflict('Source file changed; upload a new document')
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
    # Only original native/candidate IDs from this exact PDF. Never crop identity.
    ref={'version':1,'file_sha256':doc.content_hash,'page_no':n,
         'ids':sorted(ids),'regions':sorted(regions)}
    key=hashlib.sha256(json.dumps(ref,sort_keys=True).encode()).hexdigest()
    return key,ref,list(box),reason.strip(),candidate


async def register_reviews(db, doc, items):
    prepared=[_prepare(doc,item) for item in items]
    if len({p[0] for p in prepared})!=len(prepared):
        raise ValueError('Duplicate review source in one run')
    await db.scalar(select(Document).where(Document.id==doc.id).with_for_update())
    existing={r.source_key:r for r in (await db.scalars(select(DocumentReview)
        .where(DocumentReview.document_id==doc.id).with_for_update()
        .execution_options(populate_existing=True))).all()}
    if any(row.file_sha256 != doc.content_hash for row in existing.values()):
        raise ReviewConflict('Source file changed; upload a new document')
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


async def resolve_review(db, doc, review_id, *, revision, status, content, teacher_id, embed_fn):
    """Publish one reviewed source atomically; never guess overlapping ownership."""
    import uuid
    from datetime import datetime, timezone
    from sqlalchemy import delete
    from backend.app.models import DocChunk, DocumentStatus
    from backend.worker.chunking import Chunk, MAX_CHUNK_CHARS, _split_text
    from backend.worker.ingest import _embed_batches, _source_fingerprint, _tsv_value

    if status not in ('confirmed','ignored'):
        raise ValueError('Choose confirmed or ignored')
    if status=='confirmed' and (not isinstance(content,str) or not 1<=len(content.strip())<=20000):
        raise ValueError('Provide final content, at most 20000 characters')
    await db.scalar(select(Document).where(Document.id==doc.id).with_for_update())
    row=await db.scalar(select(DocumentReview).where(DocumentReview.id==review_id,
        DocumentReview.document_id==doc.id).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise LookupError('Review not found')
    if row.revision!=revision or row.status!='pending':
        raise ReviewConflict('Review changed; reload before submitting')
    if row.file_sha256!=doc.content_hash:
        raise ReviewConflict('Source file changed')
    if status=='confirmed':
        current=list((await db.scalars(select(DocChunk).where(DocChunk.document_id==doc.id,
            DocChunk.page_no==row.page_no).with_for_update().execution_options(populate_existing=True))).all())
        ids=set(row.source_ref['ids']);regions=set(row.source_ref['regions']); matched=[]
        for chunk in current:
            ref=chunk.source_ref or {}
            if not ref:
                raise ReviewConflict('Legacy source cannot be matched; keep existing page')
            ci=set(ref.get('ids',[]));cr={i.split(':',1)[0] for i in ref.get('regions',[])}
            if ids & ci or regions & cr:
                if ci!=ids or cr!=regions or ref.get('file_sha256')!=row.file_sha256 or chunk.edited_by_teacher:
                    raise ReviewConflict('Source overlaps existing or teacher-edited content; keep existing page')
                matched.append(chunk)
        parts=list(_split_text(content.strip(),MAX_CHUNK_CHARS,code=True))
        embeddings=await _embed_batches(parts,embed_fn)
        matched.sort(key=lambda c:c.chunk_index)
        base_ref=(matched[0].source_ref if matched else {**row.source_ref,'version':2,
            'source_ranges':[],'identity_uncertain':not bool(ids or regions)})
        original=matched[0].source_text if matched else ''
        if ids and not matched:
            raise ReviewConflict('Native source not indexed; process this page before confirming replacement')
        start=min((c.chunk_index for c in matched),default=max((c.chunk_index for c in current),default=-1)+1)
        if matched:
            await db.execute(delete(DocChunk).where(DocChunk.id.in_([c.id for c in matched])))
        for index,(text,vector) in enumerate(zip(parts,embeddings)):
            ref={**base_ref,'part':index,'parts':len(parts)}
            value=Chunk(text,row.page_no,None,source_text=original,source_ref=ref)
            db.add(DocChunk(id=matched[index].id if index<len(matched) else uuid.uuid4(),
                document_id=doc.id,class_id=doc.class_id,lab_id=doc.lab_id,chunk_index=start+index,
                content=text,source_text=original,source_ref=ref,source_fingerprint=_source_fingerprint(value),
                embedding=vector,tsv=_tsv_value(db,text,doc.language),page_no=row.page_no,edited_by_teacher=True))
        row.final_content=content.strip()
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


def render_review_image(doc, row):
    from pathlib import Path
    import pymupdf
    if hashlib.sha256(Path(doc.storage_path).read_bytes()).hexdigest()!=row.file_sha256:
        raise ReviewConflict('Original file changed; screenshot unavailable')
    with pymupdf.open(doc.storage_path) as pdf:
        if not 1<=row.page_no<=len(pdf):
            raise ReviewConflict('Original physical page unavailable')
        page=pdf[row.page_no-1]
        box=pymupdf.Rect(row.bbox)
        if box.is_empty or not page.rect.contains(box):
            raise ReviewConflict('Registered region is outside the original page')
        scale=min(4,2000/max(box.width,box.height))
        return page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),clip=box,alpha=False).tobytes('png')
