"""Prepare pages independently; publish successful pages together under a lock."""
import uuid
from collections import Counter
from dataclasses import replace
from openai import APIConnectionError, APITimeoutError
from sqlalchemy import select, delete
from backend.app.models import DocChunk, Document
from backend.worker.page_reader import chunk_read_pages, PageReadError


class NoSearchableContent(PageReadError):
    """A read page has no publishable text; preserve its original failure reason."""


def snapshot(row):
    return (row.id, row.content, row.edited_by_teacher, row.source_fingerprint, row.context)


def review_item_ref(item):
    """The report's program-built v3 source, or the legacy v1 form of older reports."""
    return item.get('source_ref') or {'version': 1, 'file_sha256': item['file_sha256'], 'page_no': item['page_no'],
                                      'ids': sorted(item['ids']), 'regions': sorted(item['region_ids'])}


def _group_ref(page, group, digest):
    from backend.worker.visual_sources import build_source_ref, uses_v3
    if uses_v3(page.reader_version):
        return build_source_ref(page.source, group.ids, group.region_ids, file_sha256=digest)
    return {'version': 1, 'file_sha256': digest, 'page_no': page.source.page_no,
            'ids': sorted(group.ids), 'regions': sorted(group.region_ids)}


def _protects(row_ref, status, review_ref):
    """Unresolved or teacher-confirmed reviews keep the old index of their source.

    A confirmed page-level review owns only the teacher's own page note, never the
    native blocks of that page. Anything not provably disjoint stays protected.
    """
    from backend.worker.visual_sources import compare_sources
    state = compare_sources(row_ref, review_ref)
    if review_ref.get('scope') == 'page' and status == 'confirmed':
        return state == 'exact'
    return state != 'disjoint'


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
    db_reviews_by_page = {}
    if has_review_protocol:
        from backend.app.models import DocumentReview
        records = list((await db.scalars(select(DocumentReview).where(DocumentReview.document_id == doc.id))).all())
        if any(row.file_sha256 != digest for row in records):
            raise TeacherEditConflict('Source file changed; upload it as a new document.')
        for row in records:
            db_reviews_by_page.setdefault(row.page_no, []).append((row.status, row.source_ref))
            review_before.setdefault(row.page_no, []).append((str(row.id), row.revision, row.status))
    from backend.worker.visual_sources import compare_sources, legacy_match
    # Visual questions already answered "no index needed" for a whole page are not asked again.
    ignored_pages = {n for n, rows in db_reviews_by_page.items()
                     if any(s == 'ignored' and ref.get('scope') == 'page' for s, ref in rows)}
    # A new review may repeat an existing one exactly, or be clearly separate. A
    # partial overlap is never guessed: no overlapping task is created, the page stays.
    review_conflicts = {n for n, items in reviews_by_page.items() if n not in ignored_pages
                        for ref in map(review_item_ref, items) for status, old_ref in db_reviews_by_page.get(n, [])
                        if (status == 'pending' or old_ref.get('scope') != 'page')
                        and compare_sources(ref, old_ref) in ('overlap', 'uncertain')}
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
            db_reviews = db_reviews_by_page.get(n, [])
            run_refs = [] if n in ignored_pages else [review_item_ref(r) for r in reviews_by_page.get(n, [])]
            if n in review_conflicts:
                raise TeacherEditConflict('review_source_conflict: new review overlaps an existing review; keep previous page')
            ignored = [ref for status, ref in db_reviews if status == 'ignored']
            if ignored:
                from backend.worker.page_reader import _native_group_text
                groups = []
                for group in page.groups:
                    if group.kind in ('text', 'code'):
                        groups.append(group)
                        continue
                    group_ref = _group_ref(page, group, digest)
                    hit = False
                    for ref in ignored:
                        state = compare_sources(group_ref, ref)
                        if state == 'disjoint':
                            continue
                        if state != 'exact' and ref.get('scope') != 'page':
                            raise TeacherEditConflict('review_source_conflict: ignored visual now crosses other sources; keep previous page')
                        hit = True
                    if not hit:
                        groups.append(group)
                    elif group.ids:
                        # Only the visual part is ignored; the native text stays searchable.
                        lines = [line for line in page.source.lines if line.id in set(group.ids)]
                        groups.append(replace(group, kind='text', text=_native_group_text(lines, 'code')))
                page = replace(page, groups=tuple(groups))
            chunks = chunk_read_pages([page], file_sha256=digest)
            if not chunks:
                raise NoSearchableContent('No searchable content')
            chunks = [replace(c, source_ref={**(c.source_ref or {}), 'file_sha256': digest}) for c in chunks]
            keys = [_source_fingerprint(c) for c in chunks]
            counts = Counter(keys)
            previous = by_page.get(n, [])
            # Database reviews still pending or confirmed, plus this run's reviews,
            # decide which old sources keep their existing text and embedding.
            protecting = [(s, ref) for s, ref in db_reviews if s in ('pending', 'confirmed')]
            protecting += [('pending', ref) for ref in run_refs]
            keep = [row for row in previous if any(_protects(row.source_ref, s, ref) for s, ref in protecting)]
            # A new chunk is dropped only when an old kept source is exactly the same.
            # A teacher's page note owns no native line and never covers new chunks.
            owners = [row for row in keep if (row.source_ref or {}).get('scope') != 'page']
            fresh = []
            for c in chunks:
                states = {compare_sources(c.source_ref, row.source_ref) for row in owners}
                if states & {'overlap', 'uncertain'}:
                    raise TeacherEditConflict('Review crosses newly grouped sources; keep previous page')
                if 'exact' not in states:
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
            reason = ({'reason': outcomes.get(n, {}).get('reason') or 'no_searchable_content'}
                      if isinstance(exc, NoSearchableContent) else {})
            outcomes[n] = dict(page_no=n, status='failed' if network else 'needs_review', error=str(exc), **reason)
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
        visual = {'visual_processed': False} if outcomes.get(n, {}).get('visual_processed') is False else {}
        outcomes[n] = dict(page_no=n, status='partial' if reviews_by_page.get(n) else 'updated', error=None,
                           retained_chunks=len(preserved.get(n, [])), **visual)
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
    review_rows = []
    review_summary = {}
    if has_review_protocol:
        from backend.app.services.document_reviews import register_reviews
        from backend.app.models import DocumentReview
        # Pages with a source conflict register nothing; the old record stays the task.
        registered_items = [r for r in review_items if r['page_no'] not in review_changed
                            and r['page_no'] not in review_conflicts and r['page_no'] not in ignored_pages]
        registered_rows = await register_reviews(db, doc, registered_items)
        from backend.app.services.document_index_state import bind_review_dependencies
        bind_review_dependencies(report['page_results'], registered_items, registered_rows)
        review_rows = list((await db.scalars(select(DocumentReview).where(DocumentReview.document_id == doc.id))).all())
        review_summary = {state: sum(r.status == state for r in review_rows) for state in ('pending', 'confirmed', 'ignored')}
    doc.ingest_report = {'page_reader':report, 'chunk_count':len(rows), 'ingest_tokens_est':tokens,
        'token_budget':token_budget, 'over_budget': token_budget is not None and tokens > token_budget,
        'page_summary': {'total':total, 'updated':updated, 'retained':retained, 'missing':missing},
        **({'review_summary': review_summary} if has_review_protocol else {})}
    from backend.app.services.document_index_state import apply_document_index_summary, summarize_document_index
    apply_document_index_summary(doc, summarize_document_index(page_count=total, chunks=rows, reviews=review_rows,
                                                               page_results=report['page_results']))
    await db.commit()
