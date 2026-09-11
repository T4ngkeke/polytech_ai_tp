"""One status rule for ingestion and teacher decisions; history is never erased."""
from types import SimpleNamespace as NS

from backend.app.models import DocumentStatus
from backend.app.services.document_index_state import apply_document_index_summary, summarize_document_index


def chunk(n):
    return NS(page_no=n)


def review(n, status='pending', scope='range'):
    return NS(page_no=n, status=status, source_ref={'version': 3, 'scope': scope})


def result(n, status='updated', **extra):
    return {'page_no': n, 'status': status, **extra}


def summary(page_count=2, chunks=(1, 2), reviews=(), results=None):
    results = [result(n) for n in range(1, page_count + 1)] if results is None else results
    return summarize_document_index(page_count=page_count, chunks=[chunk(n) for n in chunks],
                                    reviews=list(reviews), page_results=results)


def test_last_resolved_review_makes_document_complete():
    pending = summary(reviews=[review(1)], results=[result(1, 'partial'), result(2)])
    assert pending['status'] == 'partially_indexed' and pending['pending_review_count'] == 1
    done = summary(reviews=[review(1, 'confirmed')], results=[result(1, 'partial'), result(2)])
    assert done['status'] == 'indexed'
    assert done['available_pages'] == [1, 2] and done['missing_pages'] == [] and done['pending_review_count'] == 0


def test_open_page_problem_blocks_completion_without_any_review():
    retained = summary(results=[result(1), result(2, 'retained', error='review_source_conflict: overlap')])
    assert retained['status'] == 'partially_indexed' and retained['retained_pages'] == [2]
    unseen = summary(results=[result(1), result(2, visual_processed=False)])
    assert unseen['status'] == 'partially_indexed'
    assert unseen['problem_pages'] == [2]


def test_no_index_page_is_not_an_unknown_missing_page():
    concluded = summary(chunks=(1,), reviews=[review(2, 'ignored', 'page')],
                        results=[result(1), result(2, 'needs_review', error='No searchable content')])
    assert concluded['no_index_pages'] == [2] and concluded['missing_pages'] == []
    assert concluded['status'] == 'indexed'
    # Ignoring one visual range does not conclude a page that has no content.
    open_page = summary(chunks=(1,), reviews=[review(2, 'ignored', 'range')],
                        results=[result(1), result(2, 'needs_review', error='No searchable content')])
    assert open_page['missing_pages'] == [2] and open_page['status'] == 'partially_indexed'


def test_page_conclusion_does_not_close_other_problems():
    state = summary(reviews=[review(2, 'ignored', 'page')],
                    results=[result(1), result(2, 'retained', error='review_source_conflict: overlap')])
    assert state['status'] == 'partially_indexed' and state['problem_pages'] == [2]


def test_no_content_needs_review_and_unknown_page_count_is_never_complete():
    assert summary(chunks=(), results=[result(1, 'needs_review'), result(2, 'needs_review')])['status'] == 'needs_review'
    assert summarize_document_index(page_count=None, chunks=[chunk(1)], reviews=[], page_results=[])['status'] == 'partially_indexed'


def test_apply_writes_current_state_and_keeps_history():
    history = {'page_summary': {'total': 2, 'updated': [1], 'retained': [2], 'missing': []},
               'page_reader': {'page_results': [result(1), result(2, 'retained')]}}
    doc = NS(status=None, error_message='old', ingest_report=dict(history))
    state = summary(results=[result(1), result(2)])
    apply_document_index_summary(doc, state)
    assert doc.status == DocumentStatus.indexed and doc.error_message is None
    assert doc.ingest_report['index_state'] == state
    assert doc.ingest_report['page_summary'] == history['page_summary']
    assert doc.ingest_report['page_reader'] == history['page_reader']
    partial = summary(results=[result(1), result(2, 'retained')])
    apply_document_index_summary(doc, partial)
    assert doc.status == DocumentStatus.partially_indexed and '2' in doc.error_message
