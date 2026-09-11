"""Current usable state of a page-read document; one rule for ingestion and review.

The processing report (page_results, page_summary) is history and is kept as is.
This summary says what is usable now and what is still open.
"""
from collections import Counter

from backend.app.models import DocumentStatus

_DONE = ('read', 'updated', 'partial')


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
                      and not (n in no_index))
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
