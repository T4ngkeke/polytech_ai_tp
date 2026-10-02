"""Current usable state of a page-read document; one rule for ingestion and review.

The processing report (page_results, page_summary) is history and is kept as is.
This summary says what is usable now and what is still open.
"""
from collections import Counter

from backend.app.models import DocumentStatus

_DONE = ('read', 'updated', 'partial')


# Only these failures can be answered by reviewing the complete page. Service,
# extraction and source conflicts remain open until their own repair succeeds.
_REVIEWABLE = {'render_failure', 'content_review', 'protocol_failure', 'budget_exhausted', 'no_searchable_content'}


def bind_review_dependencies(page_results, items, rows):
    """Tie a historical failure to the pending revision created by this ingestion."""
    by_source = {r.source_key: r for r in rows}
    from backend.worker.visual_sources import source_group_key
    for outcome in page_results:
        reason = outcome.get('reason')
        if not reason and outcome.get('error') == 'No searchable content':
            reason = outcome['reason'] = 'no_searchable_content'
        if reason not in _REVIEWABLE or outcome.get('visual_processed') is False:
            continue
        dependencies = []
        for item in items:
            ref = item.get('source_ref') or {}
            if item['page_no'] != outcome['page_no'] or ref.get('version') != 3 or ref.get('scope') != 'page':
                continue
            if item.get('reason_code') not in _REVIEWABLE:
                continue
            row = by_source.get(source_group_key(ref))
            if row is not None and row.status == 'pending':
                dependencies.append({'review_id': str(row.id), 'revision': row.revision,
                                     'file_sha256': row.file_sha256, 'source_key': row.source_key})
        if dependencies:
            outcome['review_dependencies'] = dependencies


def _resolved_failure(outcome, reviews):
    dependencies = outcome.get('review_dependencies', [])
    if not dependencies or outcome.get('reason') not in _REVIEWABLE or outcome.get('visual_processed') is False:
        return False
    rows = {str(r.id): r for r in reviews if hasattr(r, 'id')}
    for dependency in dependencies:
        row = rows.get(dependency['review_id'])
        if (row is None or row.page_no != outcome['page_no'] or row.status not in ('confirmed', 'ignored')
                or row.source_ref.get('scope') != 'page' or row.revision != dependency['revision'] + 1
                or row.file_sha256 != dependency['file_sha256'] or row.source_key != dependency['source_key']):
            return False
    return True


def summarize_document_index(*, page_count, chunks, reviews, page_results) -> dict:
    pages = range(1, (page_count or 0) + 1)
    available = sorted({c.page_no for c in chunks if c.page_no is not None})
    pending = Counter(r.page_no for r in reviews if r.status == 'pending')
    # Only a decision on the whole page is a conclusion for a page without content.
    concluded = {r.page_no for r in reviews
                 if r.status in ('confirmed', 'ignored') and (r.source_ref or {}).get('scope') == 'page'}
    results = {p['page_no']: p for p in page_results}
    no_index = sorted(n for n in pages if n not in available and n in concluded and not pending[n])
    missing = sorted(n for n in pages if n not in available and n not in no_index)
    # The latest processing of a page failed or skipped its visual part. A teacher's
    # page conclusion answers only "this page has no content", never other problems.
    problems = sorted(n for n, p in results.items()
                      if (p.get('status') not in _DONE or p.get('visual_processed') is False)
                      and not _resolved_failure(p, reviews)
                      and not (n in no_index and (p.get('reason') == 'no_searchable_content'
                               or (not p.get('reason') and p.get('error') == 'No searchable content'))
                               and p.get('visual_processed') is not False))
    retained = sorted(n for n, p in results.items() if p.get('status') == 'retained')
    pending_count = sum(pending.values())
    if not available:
        status = DocumentStatus.needs_review.value
    elif pending_count or missing or problems or not page_count:
        status = DocumentStatus.partially_indexed.value
    else:
        status = DocumentStatus.indexed.value
    return {'status': status, 'page_count': page_count, 'available_pages': available,
            'missing_pages': missing, 'no_index_pages': no_index, 'pending_review_count': pending_count,
            'retained_pages': retained, 'problem_pages': problems}


def apply_document_index_summary(doc, summary) -> None:
    doc.status = DocumentStatus(summary['status'])
    doc.ingest_report = {**(doc.ingest_report or {}), 'index_state': summary}
    if doc.status == DocumentStatus.indexed:
        doc.error_message = None
        return
    parts = [f"{len(summary['available_pages'])}/{summary['page_count'] or '?'} pages usable"]
    for label, key in (('missing', 'missing_pages'), ('kept from the previous run', 'retained_pages'),
                       ('still open', 'problem_pages'), ('no indexing needed', 'no_index_pages')):
        if summary[key]:
            parts.append(f'{label}: {summary[key]}')
    parts.append(f"{summary['pending_review_count']} review tasks pending")
    doc.error_message = '; '.join(parts)
