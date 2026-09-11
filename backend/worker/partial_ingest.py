"""Prepare pages independently; publish successful pages together under a lock."""
import uuid
from collections import Counter
from dataclasses import replace
from openai import APIConnectionError, APITimeoutError
from sqlalchemy import select, delete
from backend.app.models import DocChunk, Document, DocumentStatus
from backend.worker.page_reader import chunk_read_pages, PageReadError


def snapshot(row):
    return (row.id, row.content, row.edited_by_teacher, row.source_fingerprint, row.context)


async def ingest_pages(db, doc, pages, report, *, embed_fn, context_fn, token_budget):
    from backend.worker.ingest import (_source_fingerprint, _context_scope, _augmented,
        _embed_batches, _tsv_value, _estimate_tokens, TeacherEditConflict, EmbeddingResultError)
    from backend.worker.context import clean_context, heading_context
    digest = report['source_sha256']
    review_items = report.get('reviews', [])
    has_review_protocol = 'reviews' in report
    if has_review_protocol and doc.content_hash != digest:
        raise TeacherEditConflict('Source file changed; upload it as a new document.')
    reviews_by_page = {}
    for review in review_items:
        reviews_by_page.setdefault(review['page_no'], []).append(review)
    review_before = {}
    ignored_by_page = {}
    if has_review_protocol:
        from backend.app.models import DocumentReview
        records = list((await db.scalars(select(DocumentReview).where(DocumentReview.document_id == doc.id))).all())
        if any(row.file_sha256 != digest for row in records):
            raise TeacherEditConflict('Source file changed; upload it as a new document.')
        for row in records:
            if row.status == 'ignored':
                ignored_by_page.setdefault(row.page_no, []).append(row.source_ref)
            review_before.setdefault(row.page_no, []).append((str(row.id), row.revision, row.status))
    preserved = {}
    old_report = doc.ingest_report or {}
    old_hash = old_report.get('page_reader', {}).get('source_sha256')
    if old_hash and old_hash != digest:
        raise TeacherEditConflict('Source file changed; upload it as a new document.')
    old = list((await db.scalars(select(DocChunk).where(DocChunk.document_id == doc.id))).all())
    if any(c.source_ref and c.source_ref.get('file_sha256') != digest for c in old):
        raise TeacherEditConflict('Source file changed; upload it as a new document.')
    if any(c.page_no is None for c in old):
        raise TeacherEditConflict('Legacy chunks have no page identity; review before page replacement.')
    by_page = {}
    for row in old:
        by_page.setdefault(row.page_no, []).append(row)
    before = {n: sorted((snapshot(r) for r in rows), key=lambda x: str(x[0])) for n, rows in by_page.items()}
    outcomes = {r['page_no']: dict(r) for r in report['page_results']}
    prepared = {}
    tokens = 0
    failures = 0
    for page in pages:
        n = page.source.page_no
        if failures >= 3:
            outcomes[n] = dict(page_no=n, status='not_processed', error='Three consecutive service failures')
            continue
        try:
            if ignored_by_page.get(n):
                from backend.worker.page_reader import _native_group_text
                groups = []
                for group in page.groups:
                    if group.kind in ('text', 'code'):
                        groups.append(group)
                        continue
                    ids, regions = set(group.ids), set(group.region_ids)
                    overlaps = [ref for ref in ignored_by_page[n]
                                if ids & set(ref['ids']) or regions & set(ref['regions'])]
                    if not overlaps:
                        groups.append(group)
                        continue
                    allowed_ids = {i for ref in overlaps for i in ref['ids']}
                    allowed_regions = {r for ref in overlaps for r in ref['regions']}
                    if not ids <= allowed_ids or not regions <= allowed_regions:
                        raise TeacherEditConflict('Ignored visual now crosses other sources; keep previous page')
                    if ids:
                        lines = [line for line in page.source.lines if line.id in ids]
                        groups.append(replace(group, kind='text', text=_native_group_text(lines, 'code')))
                page = replace(page, groups=tuple(groups))
            chunks = chunk_read_pages([page])
            if not chunks:
                raise PageReadError('No searchable content')
            chunks = [replace(c, source_ref={**(c.source_ref or {}), 'file_sha256': digest}) for c in chunks]
            keys = [_source_fingerprint(c) for c in chunks]
            counts = Counter(keys)
            previous = by_page.get(n, [])
            keep = []
            for row in previous:
                for review in reviews_by_page.get(n, []):
                    ref = row.source_ref or {}
                    if not ref:
                        raise TeacherEditConflict('Cannot locate review in legacy page; keep previous page')
                    ids, regions = set(ref.get('ids', [])), {r.split(':', 1)[0] for r in ref.get('regions', [])}
                    if ids & set(review['ids']) or regions & set(review['region_ids']) or not (review['ids'] or review['region_ids']):
                        keep.append(row)
                        break
            # Preserve existing text/embedding for unresolved sources. New chunks
            # can be discarded only when fully covered by those kept sources.
            keep_ids = {i for row in keep for i in (row.source_ref or {}).get('ids', [])}
            keep_regions = {r.split(':', 1)[0] for row in keep for r in (row.source_ref or {}).get('regions', [])}
            fresh = []
            for c in chunks:
                ids = set(c.source_ref.get('ids', []))
                regions = {r.split(':', 1)[0] for r in c.source_ref.get('regions', [])}
                if ids & keep_ids or regions & keep_regions:
                    if not ids <= keep_ids or not regions <= keep_regions:
                        raise TeacherEditConflict('Review crosses newly grouped sources; keep previous page')
                else:
                    fresh.append(c)
            chunks = fresh
            keys = [_source_fingerprint(c) for c in chunks]
            counts = Counter(keys)
            preserved[n] = keep
            previous = [row for row in previous if row not in keep]
            matches = {}
            # Legacy hashes can only be matched against unmodified native text.
            legacy = Counter(_source_fingerprint(replace(c, content=c.source_text or '', source_text=None, source_ref=None)) for c in chunks)
            old_counts = Counter(r.source_fingerprint for r in previous)
            for row in previous:
                key = row.source_fingerprint
                target = key if key and counts[key] == 1 and old_counts[key] == 1 else None
                if target is None and row.source_ref is None and key and old_counts[key] == 1 and legacy[key] == 1:
                    target = next(keys[i] for i,c in enumerate(chunks)
                                  if _source_fingerprint(replace(c, content=c.source_text or '', source_text=None, source_ref=None)) == key)
                if target is None and row.source_ref:
                    from backend.worker.visual_sources import legacy_match
                    proven = [(k,c) for k,c in zip(keys,chunks) if counts[k]==1 and legacy_match(row,c)]
                    if len(proven)==1:
                        k,c=proven[0]
                        if sum(legacy_match(r,c) or r.source_fingerprint==k for r in previous)==1:
                            target=k
                if target:
                    candidate=chunks[keys.index(target)]
                    if candidate.source_ref.get('identity_uncertain'):
                        target=None
                if target and row.edited_by_teacher and row.source_ref:
                    candidate = chunks[keys.index(target)]
                    if row.source_ref.get('split_signature') != candidate.source_ref.get('split_signature'):
                        target = None
                if target is None and row.edited_by_teacher:
                    raise TeacherEditConflict('Teacher-edited source cannot be matched uniquely')
                if target:
                    matches[target] = row
            effective = [replace(c, content=matches[k].content) if k in matches and matches[k].edited_by_teacher else c
                         for c,k in zip(chunks,keys)]
            contexts = []
            for i,c in enumerate(effective):
                value = await context_fn(_context_scope(effective,i), c.content) if context_fn else None
                contexts.append((clean_context(value) or heading_context(c)) if value else None)
                if value:
                    tokens += _estimate_tokens(_context_scope(effective,i), c.content, value)
            augmented = [_augmented(ctx,c.content) for ctx,c in zip(contexts,effective)]
            embeddings = await _embed_batches(augmented, embed_fn)
            prepared[n] = [(c,k,contexts[i],embeddings[i],augmented[i],matches.get(k)) for i,(c,k) in enumerate(zip(effective,keys))]
            failures = 0
        except (PageReadError, TeacherEditConflict, EmbeddingResultError, APIConnectionError, APITimeoutError, TimeoutError) as exc:
            network = isinstance(exc, (APIConnectionError, APITimeoutError, TimeoutError))
            failures = failures + 1 if network else 0
            outcomes[n] = dict(page_no=n, status='failed' if network else 'needs_review', error=str(exc))
    # Serialize publication with teacher edits, then reload to detect edits made
    # while the worker waited on model/embedding calls. No long model call holds this lock.
    await db.scalar(select(Document).where(Document.id == doc.id).with_for_update())
    current = list((await db.scalars(select(DocChunk).where(DocChunk.document_id == doc.id)
                    .with_for_update().execution_options(populate_existing=True))).all())
    current_by_page = {}
    for row in current:
        current_by_page.setdefault(row.page_no, []).append(row)
    review_changed = set()
    if has_review_protocol:
        records = list((await db.scalars(select(DocumentReview).where(DocumentReview.document_id == doc.id)
            .with_for_update().execution_options(populate_existing=True))).all())
        review_now = {}
        for row in records:
            review_now.setdefault(row.page_no, []).append((str(row.id), row.revision, row.status))
        review_changed = {n for n in set(review_before) | set(review_now)
                          if sorted(review_before.get(n, [])) != sorted(review_now.get(n, []))}
    for n in list(prepared):
        now = sorted((snapshot(r) for r in current_by_page.get(n, [])), key=lambda x: str(x[0]))
        if now != before.get(n, []) or n in review_changed:
            del prepared[n]
            outcomes[n] = dict(page_no=n, status='needs_review', error='Teacher changed this page during processing')
    for n, items in prepared.items():
        keep_ids = [row.id for row in preserved.get(n, [])]
        statement = delete(DocChunk).where(DocChunk.document_id == doc.id, DocChunk.page_no == n)
        if keep_ids:
            statement = statement.where(DocChunk.id.not_in(keep_ids))
        await db.execute(statement)
        for i,(c,key,ctx,embedding,aug,prev) in enumerate(items):
            db.add(DocChunk(id=prev.id if prev else uuid.uuid4(), document_id=doc.id,
                class_id=doc.class_id, lab_id=doc.lab_id, chunk_index=i,
                content=c.content, source_text=c.source_text, source_ref=c.source_ref,
                source_fingerprint=key, section=c.section, context=ctx, embedding=embedding,
                tsv=_tsv_value(db,aug,doc.language), page_no=n,
                edited_by_teacher=prev.edited_by_teacher if prev else False))
        outcomes[n] = dict(page_no=n, status='partial' if reviews_by_page.get(n) else 'updated', error=None, retained_chunks=len(preserved.get(n, [])))
    await db.flush()
    rows = list((await db.scalars(select(DocChunk).where(DocChunk.document_id == doc.id)
               .order_by(DocChunk.page_no, DocChunk.chunk_index))).all())
    for i,row in enumerate(rows):
        row.chunk_index = i
    available = {r.page_no for r in rows}
    updated = sorted(prepared)
    retained = sorted(available - set(updated))
    total = report['page_count']
    missing = sorted(set(range(1,total+1)) - available)
    for n in retained:
        outcomes[n] = {**outcomes.get(n, {'page_no':n}), 'status':'retained'}
    report = {**report, 'page_results': [outcomes[n] for n in sorted(outcomes)],
              'chunks': [{'chunk_id':str(r.id), 'source_ref':r.source_ref} for r in rows]}
    # Preserve old audit pages for retained data; new failures stay in page_results.
    prior_pages = {p['source']['page_no']:p for p in old_report.get('page_reader',{}).get('pages',[])}
    new_pages = {p['source']['page_no']:p for p in report.get('pages',[])}
    report['pages'] = [new_pages[n] if n in updated else prior_pages[n]
                       for n in sorted(available) if (n in updated and n in new_pages) or (n not in updated and n in prior_pages)]
    doc.page_count = total
    pending = 0
    review_summary = {}
    if has_review_protocol:
        from backend.app.services.document_reviews import register_reviews
        from backend.app.models import DocumentReview
        await register_reviews(db, doc, [r for r in review_items if r['page_no'] not in review_changed])
        review_rows = list((await db.scalars(select(DocumentReview).where(DocumentReview.document_id == doc.id))).all())
        pending = sum(r.status == 'pending' for r in review_rows)
        review_summary = {state: sum(r.status == state for r in review_rows) for state in ('pending', 'confirmed', 'ignored')}
    doc.ingest_report = {'page_reader':report, 'chunk_count':len(rows), 'ingest_tokens_est':tokens,
        'token_budget':token_budget, 'over_budget': token_budget is not None and tokens > token_budget,
        'page_summary': {'total':total, 'updated':updated, 'retained':retained, 'missing':missing},
        **({'review_summary': review_summary} if has_review_protocol else {})}
    doc.status = (DocumentStatus.indexed if len(updated) == total and not pending else
                  DocumentStatus.partially_indexed if rows else DocumentStatus.needs_review)
    doc.error_message = None if doc.status == DocumentStatus.indexed else f'{len(updated)}/{total} pages updated; retained {retained}; missing {missing}; {pending} review tasks pending'
    await db.commit()
