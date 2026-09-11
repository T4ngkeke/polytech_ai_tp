"""Browser regression on v9 assets; all API traffic is mocked (no DB/model writes).

TEACHER_TEST_URL must point to v9; defaults to its mapped port 8080.
A local DNS alias exercises ordinary HTTP, where randomUUID is unavailable.
"""
import os
from urllib.parse import urlsplit
from playwright.sync_api import expect, sync_playwright


def main():
    target = os.getenv('TEACHER_TEST_URL', 'http://127.0.0.1:8080')
    parsed = urlsplit(target)
    assert parsed.hostname == '127.0.0.1', 'Use an explicit loopback v9 endpoint'
    with sync_playwright() as p:
        browser = p.chromium.launch(args=[
            '--host-resolver-rules=MAP v9-browser.test 127.0.0.1', '--no-proxy-server'])
        try:
            for origin, secure in [(target, True), (f'http://v9-browser.test:{parsed.port}', False)]:
                page = browser.new_page()
                errors, sent = [], []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.add_init_script("localStorage.setItem('edu_llm_token', 'browser-test')")

                def api(route):
                    path = urlsplit(route.request.url).path
                    if path == '/api/auth/me':
                        route.fulfill(json={'id': 'teacher', 'username': 'teacher', 'role': 'teacher'})
                    elif path == '/api/teacher/classes':
                        route.fulfill(json=[{'id': 'class', 'name': 'Browser test class'}])
                    elif path == '/api/teacher/classes/class/labs':
                        route.fulfill(json=[{'id': 'lab', 'name': 'Browser test lab', 'is_active': True}])
                    elif path.endswith('/test-session'):
                        route.fulfill(status=201, json={'session_id': 'test-session'})
                    elif path == '/api/chat/stream':
                        sent.append(route.request.post_data_json)
                        route.fulfill(content_type='text/event-stream', body=(
                            'event: status\ndata: {"route":"direct"}\n\n'
                            'data: "Preview answer"\n\n'
                            'event: done\ndata: {}\n\n'))
                    else:
                        route.fulfill(json=[])

                page.route('**/api/**', api)
                page.goto(origin + '/teacher')
                assert page.evaluate('isSecureContext') == secure
                page.get_by_text('Browser test class', exact=True).click()
                page.get_by_text('Browser test lab', exact=True).click()
                page.get_by_role('button', name='Test drive', exact=True).click()
                page.get_by_role('button', name='Start test drive', exact=True).click()
                for index in range(2):
                    page.get_by_placeholder('Message the tutor…').fill(f'Explain a loop {index}')
                    page.get_by_role('button', name='Send', exact=True).click()
                    try:
                        expect(page.get_by_text('Preview answer', exact=True)).to_have_count(index + 1)
                    except AssertionError:
                        raise AssertionError(f'origin={origin}; browser errors={errors}; requests={sent}')
                assert len(sent) == 2 and all(s['session_id'] == 'test-session' for s in sent)
                assert not errors, errors
                print(f'PASS: {origin}, two messages sent and displayed; secure={secure}')
                page.close()
        finally:
            browser.close()


if __name__ == '__main__':
    main()
