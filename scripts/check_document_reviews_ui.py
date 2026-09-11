"""Teacher review browser contract; mocked API, isolated Vite, no database writes."""
import base64
import os
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.add_init_script("localStorage.setItem('edu_llm_token','test')")
    review = dict(id='r', page_no=2, bbox=[10, 20, 100, 120], reason='Arrow direction unclear', candidate_content='Candidate description', status='pending', revision=1, final_content=None)
    submissions = []
    def route_api(route):
        path = urlsplit(route.request.url).path
        if path == '/api/auth/me': value = dict(id='t', username='teacher', role='teacher')
        elif path == '/api/teacher/classes': value = [dict(id='c', name='Test class')]
        elif path.endswith('/classes/c/labs'): value = [dict(id='l', name='Test lab', is_active=True)]
        elif path.endswith('/labs/l/documents'): value = [dict(id='d', filename='partial.pdf', status='partially_indexed', page_count=3, chunk_count=1, ingest_report={'page_summary':{'total':3,'updated':[1,2],'retained':[],'missing':[3]}, 'review_summary':{'pending':int(review['status']=='pending')}})]
        elif path.endswith('/reviews/r/image'):
            assert route.request.headers.get('authorization') == 'Bearer test'
            route.fulfill(content_type='image/png', body=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='))
            return
        elif path.endswith('/reviews/r') and route.request.method == 'PUT':
            body = route.request.post_data_json
            submissions.append(body)
            if len(submissions) == 1:
                review['revision'] = 2
                route.fulfill(status=409, json={'detail': 'Review changed. Reload before saving.'})
                return
            if len(submissions) == 2:
                route.fulfill(status=500, json={'detail': 'Embedding unavailable'})
                return
            assert body['revision'] == 2
            review.update(status=body['status'], revision=3, final_content=body.get('content'))
            value = review
        elif path.endswith('/reviews'): value = [review]
        else: value = []
        route.fulfill(json=value)
    page.route('**/api/**', route_api)
    page.goto(os.getenv('PARTIAL_UI_URL', 'http://127.0.0.1:18080') + '/teacher')
    page.get_by_text('Test class', exact=True).click()
    page.get_by_text('Test lab', exact=True).click()
    page.get_by_role('button', name='Documents', exact=True).click()
    expect(page.get_by_text('2 usable · 1 unavailable', exact=True)).to_be_visible()
    page.get_by_text('partial.pdf', exact=True).click()
    expect(page.get_by_text('Arrow direction unclear', exact=True)).to_be_visible()
    page.get_by_role('button', name='View source image').click()
    expect(page.get_by_role('img', name='Source region on page 2')).to_be_visible()
    field = page.get_by_role('textbox', name='Confirmed content')
    field.fill('Teacher verified relationship')
    page.get_by_role('button', name='Confirm & index').click()
    expect(page.get_by_role('alert')).to_contain_text('Review changed')
    expect(field).to_have_value('Teacher verified relationship')
    expect(page.get_by_role('button', name='Confirm & index')).to_be_disabled()
    page.get_by_role('button', name='Reload review').click()
    field.fill('Teacher verified relationship')
    page.get_by_role('button', name='Confirm & index').click()
    expect(page.get_by_role('alert')).to_contain_text('Embedding unavailable')
    expect(field).to_have_value('Teacher verified relationship')
    page.get_by_role('button', name='Confirm & index').click()
    expect(page.get_by_text('Page 2 · Confirmed', exact=True)).to_be_visible()
    assert len(submissions) == 3
    review.update(status='pending', revision=2, final_content=None)
    page.reload()
    page.get_by_text('Test class', exact=True).click()
    page.get_by_text('Test lab', exact=True).click()
    page.get_by_role('button', name='Documents', exact=True).click()
    page.get_by_text('partial.pdf', exact=True).click()
    page.get_by_role('button', name='No indexing needed', exact=True).click()
    expect(page.get_by_text('Page 2 · No indexing needed', exact=True)).to_be_visible()
    assert submissions[-1]['status'] == 'ignored'
    assert submissions[-1]['content'] is None
    assert not errors, errors
    print('PASS: authenticated image, stale edit, failed embedding, retry and confirmation')
    browser.close()
