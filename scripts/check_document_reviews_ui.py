"""Teacher review browser contract; mocked API, isolated frontend, no database writes."""
import base64
import os
from collections import Counter
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
BASE = os.getenv('PARTIAL_UI_URL', 'http://127.0.0.1:18080')

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.add_init_script("localStorage.setItem('edu_llm_token','test')")
    review = dict(id='r', page_no=2, bbox=[10, 20, 100, 120], reason='Arrow direction unclear',
                  candidate_content='Candidate description', status='pending', revision=1, final_content=None)
    calls = Counter()
    submissions = []
    # Scripted answers, consumed in order; afterwards the normal answer is used.
    list_answers = [(500, {'detail': 'Database busy'})]
    image_answers = [(409, {'detail': {'code': 'review_source_changed', 'message': 'Original file changed'}}),
                     'png', (500, {'detail': 'Renderer unavailable'})]
    put_answers = [(409, {'detail': 'Review changed. Reload before saving.'}),  # legacy string detail
                   (503, {'detail': {'code': 'embedding_unavailable', 'message': 'Embedding service unavailable'}}),
                   (409, {'detail': {'code': 'review_source_conflict', 'message': 'Source overlaps differently grouped content'}})]
    state = {'index_state': {'status': 'partially_indexed', 'available_pages': [1, 2], 'missing_pages': [],
                             'no_index_pages': [3], 'pending_review_count': 1, 'retained_pages': [],
                             'problem_pages': []},
             'page_summary': {'total': 3, 'updated': [1, 2], 'retained': [], 'missing': [3]}}

    def route_api(route):
        path = urlsplit(route.request.url).path
        if path == '/api/auth/me': value = dict(id='t', username='teacher', role='teacher')
        elif path == '/api/teacher/classes': value = [dict(id='c', name='Test class')]
        elif path.endswith('/classes/c/labs'): value = [dict(id='l', name='Test lab', is_active=True)]
        elif path.endswith('/labs/l/documents'):
            calls['documents'] += 1
            value = [dict(id='d', filename='partial.pdf', status='partially_indexed', page_count=3, chunk_count=1,
                          ingest_report=state)]
        elif path.endswith('/documents/d/chunks'):
            calls['chunks'] += 1
            value = []
        elif path.endswith('/reviews/r/image'):
            assert route.request.headers.get('authorization') == 'Bearer test'
            calls['image'] += 1
            answer = image_answers.pop(0) if image_answers else 'png'
            if answer == 'png':
                route.fulfill(content_type='image/png', body=PNG)
            else:
                route.fulfill(status=answer[0], json=answer[1])
            return
        elif path.endswith('/reviews/r') and route.request.method == 'PUT':
            body = route.request.post_data_json
            submissions.append(body)
            if put_answers:
                code, answer = put_answers.pop(0)
                if len(submissions) == 1:
                    review['revision'] = 2
                route.fulfill(status=code, json=answer)
                return
            assert body['revision'] == review['revision']
            review.update(status=body['status'], revision=review['revision'] + 1, final_content=body.get('content'))
            state['index_state']['pending_review_count'] = 0
            value = review
        elif path.endswith('/reviews'):
            calls['reviews'] += 1
            if list_answers:
                code, answer = list_answers.pop(0)
                route.fulfill(status=code, json=answer)
                return
            value = [review]
        else: value = []
        route.fulfill(json=value)

    def open_document():
        page.goto(BASE + '/teacher')
        page.get_by_text('Test class', exact=True).click()
        page.get_by_text('Test lab', exact=True).click()
        page.get_by_role('button', name='Documents', exact=True).click()

    page.route('**/api/**', route_api)
    open_document()
    # "No indexing needed" pages are not counted as unavailable.
    expect(page.get_by_text('2 usable · 0 unavailable · 1 not indexed', exact=True)).to_be_visible()
    page.get_by_text('partial.pdf', exact=True).click()

    # 1. The review list failed to load: retry, not a blank page.
    expect(page.get_by_role('alert')).to_contain_text('Could not load visual reviews')
    page.get_by_role('button', name='Retry loading reviews').click()
    expect(page.get_by_text('Arrow direction unclear', exact=True)).to_be_visible()

    # 2. The picture failed: clear message, retry shows it.
    page.get_by_role('button', name='View source image').click()
    expect(page.get_by_role('alert')).to_contain_text('original file changed or is missing')
    image = page.get_by_role('img', name='Source region on page 2')
    expect(image).to_have_count(0)
    page.get_by_role('button', name='Retry image').click()
    expect(image).to_be_visible()

    # 3. Stale revision (old string detail): text kept, submit blocked until reload.
    field = page.get_by_role('textbox', name='Confirmed content')
    field.fill('Teacher verified relationship')
    page.get_by_role('button', name='Confirm & index').click()
    expect(page.get_by_role('alert')).to_contain_text('Someone changed this review')
    expect(page.get_by_role('alert')).to_contain_text('Review changed')
    expect(field).to_have_value('Teacher verified relationship')
    expect(page.get_by_role('button', name='Confirm & index')).to_be_disabled()
    expect(page.get_by_role('button', name='No indexing needed')).to_be_disabled()
    page.get_by_role('button', name='Reload review').click()
    # The new revision reloads the picture; the old one must not stay on screen.
    expect(page.get_by_role('alert')).to_contain_text('Renderer unavailable')
    expect(image).to_have_count(0)
    page.get_by_role('button', name='Retry image').click()
    expect(image).to_be_visible()

    # 4. Embedding service down: text kept, retry allowed.
    expect(field).to_have_value('Teacher verified relationship')
    page.get_by_role('button', name='Confirm & index').click()
    expect(page.get_by_role('alert')).to_contain_text('search index service is unavailable')
    expect(field).to_have_value('Teacher verified relationship')
    expect(page.get_by_role('button', name='Confirm & index')).to_be_enabled()

    # 5. Source conflict: nothing overwritten; confirming again cannot help.
    page.get_by_role('button', name='Confirm & index').click()
    expect(page.get_by_role('alert')).to_contain_text('nothing was overwritten')
    expect(field).to_have_value('Teacher verified relationship')
    expect(page.get_by_role('button', name='Confirm & index')).to_be_disabled()
    expect(page.get_by_role('button', name='Reload review')).to_have_count(0)
    expect(page.get_by_role('button', name='No indexing needed')).to_be_enabled()

    # 6. "No indexing needed" succeeds; reviews, summary and chunks refresh.
    before = dict(calls)
    page.get_by_role('button', name='No indexing needed', exact=True).click()
    expect(page.get_by_text('Page 2 · No indexing needed', exact=True)).to_be_visible()
    expect(page.get_by_text('Visual reviews (0 pending)', exact=True)).to_be_visible()
    page.wait_for_timeout(300)
    assert submissions[-1]['status'] == 'ignored' and submissions[-1]['content'] is None
    assert calls['reviews'] > before['reviews'], calls
    assert calls['documents'] > before['documents'], calls
    assert calls['chunks'] > before['chunks'], calls

    # 7. Confirmation succeeds after a fresh start.
    review.update(status='pending', final_content=None)
    open_document()
    page.get_by_text('partial.pdf', exact=True).click()
    page.get_by_role('textbox', name='Confirmed content').fill('Teacher verified relationship')
    before = dict(calls)
    page.get_by_role('button', name='Confirm & index').click()
    expect(page.get_by_text('Page 2 · Confirmed', exact=True)).to_be_visible()
    page.wait_for_timeout(300)
    assert submissions[-1] == {'revision': review['revision'] - 1, 'status': 'confirmed',
                               'content': 'Teacher verified relationship'}
    assert calls['chunks'] > before['chunks'] and calls['documents'] > before['documents'], calls
    assert not errors, errors
    print('PASS: list retry, image retry without stale picture, stale edit, embedding retry, '
          'source conflict, ignore and confirm with refresh')
    browser.close()
