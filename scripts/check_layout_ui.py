"""Browser check of the light/dark themes and the phone layout; all API traffic is mocked.

Each main page (student chat, teacher lab, admin model map) is opened in both
themes at desktop (1440 px) and phone (390 px) width. The check fails when the
page scrolls sideways, the theme is not applied, the phone menu does not open
the navigation drawer, the student drawer is not ordered classes, sessions,
account, the token quota is pushed off screen, or the admin model map is missing.
In a teacher lab, the class name in the breadcrumb must open the class, and the
Analytics tab must show its charts and refill a student's quota; Audit shows
the latest AI report on the lab.
The admin Monitoring tab shows the real Grafana dashboards (not mocked), in the
app's theme; it needs the monitoring containers running, and MONITORING_COOKIE
(an admin's monitoring token), since Grafana is open to app admins only.
Without it, the check only makes sure Grafana is closed.

UI_TEST_URL must be a loopback frontend; it defaults to v9's mapped port 8080.
PLAYWRIGHT_CHROMIUM may name a Chromium executable when Playwright's own build
is not installed. UI_SHOTS_DIR, when set, receives a screenshot of each view
for a human look.
"""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

DESKTOP, PHONE = (1440, 900), (390, 844)

LONG_ANSWER = (
    'Une restriction garde les tuples qui vérifient une condition.\n\n'
    '| relation | attribut | type | exemple de valeur assez longue |\n'
    '| --- | --- | --- | --- |\n'
    '| Vin | cru | texte | Château Margaux, Pauillac, Saint-Émilion |\n\n'
    '```sql\nSELECT region, count(*) FROM vins GROUP BY region HAVING count(*) > 2 ORDER BY region;\n```\n'
)

MOCKS = {
    '/api/student/classes': [{'id': 'class', 'name': 'Informatique'}],
    '/api/student/classes/class/labs': [{'id': 'lab', 'name': 'TP agent', 'is_active': True}],
    # Many sessions, as in a real course: the list must scroll, not push the quota away.
    '/api/student/sessions': [{'id': 'session', 'title': 'Group by et having', 'created_at': '2026-10-15T09:00:00Z'}]
    + [{'id': f'old-{i}', 'title': f'Session {i}', 'created_at': '2026-10-01T09:00:00Z'} for i in range(60)],
    '/api/student/sessions/session': {'messages': [
        {'id': 'm1', 'sender': 'user', 'content': 'Quelle différence entre WHERE et HAVING ?'},
        {'id': 'm2', 'sender': 'llm', 'content': LONG_ANSWER},
    ]},
    '/api/student/usage': {'used': 1200, 'limit': 50000},
    '/api/teacher/classes': [{'id': 'class', 'name': 'Informatique'}],
    '/api/teacher/classes/class/labs': [{'id': 'lab', 'name': 'TP agent', 'is_active': True}],
    '/api/admin/llm/config': {'llm_model': 'ornith', 'features': {'research_agent': True, 'pdf_documents': True}},
    '/api/teacher/analytics/classes/class/details': {
        'class_id': 'class', 'lab_id': 'lab', 'period_days': 30,
        'overview': {'members': 3, 'active_7d': 2, 'active_ever': 2, 'questions': 41, 'questions_7d': 12,
                     'sessions': 9, 'thumbs_up': 3, 'thumbs_down': 1, 'at_limit_today': 1},
        'modes': {'search': 20, 'research': 15, 'direct': 6},
        'daily': [{'date': f'2026-10-{d:02d}', 'questions': d % 5} for d in range(1, 31)],
        'heatmap': [[(d * 24 + h) % 4 if 8 <= h <= 22 else 0 for h in range(24)] for d in range(7)],
        'students': [
            {'user_id': 's1', 'username': 'a.very.long.student.name@etu.univ-smb.fr', 'member': True,
             'questions': 30, 'sessions': 6, 'last_active': '2026-10-15T09:00:00Z', 'tokens': 90000,
             'thumbs_down': 1, 'modes': {}, 'used_today': 50000, 'limit_today': 50000,
             'granted_today': 0, 'at_limit': True},
            {'user_id': 's2', 'username': 'bob', 'member': True, 'questions': 11, 'sessions': 3,
             'last_active': '2026-10-12T09:00:00Z', 'tokens': 20000, 'thumbs_down': 0, 'modes': {},
             'used_today': 1000, 'limit_today': 50000, 'granted_today': 0, 'at_limit': False},
            {'user_id': 's3', 'username': 'cat', 'member': True, 'questions': 0, 'sessions': 0,
             'last_active': None, 'tokens': 0, 'thumbs_down': 0, 'modes': {}, 'used_today': 0,
             'limit_today': 50000, 'granted_today': 0, 'at_limit': False},
        ],
        'thumbs_down': [{'message_id': 'm', 'session_id': 'x', 'user_id': 's1', 'lab_id': 'lab',
                         'lab_name': 'TP agent', 'username': 'a.very.long.student.name@etu.univ-smb.fr',
                         'question': 'Pourquoi ma requête GROUP BY renvoie une erreur ' * 4, 'at': '2026-10-14T09:00:00Z'}],
        'problems': [{'session_id': 'y', 'user_id': 's2', 'username': 'bob', 'lab_id': 'lab', 'lab_name': 'TP agent',
                      'question': 'Explique Dijkstra', 'at': '2026-10-13T09:00:00Z', 'reason': 'Tool limit reached'}],
    },
    '/api/teacher/classes/class/students/s1/quota-refill': {'used': 50000, 'limit': 100000, 'granted': 50000},
    '/api/teacher/labs/lab/reports': {'report': {
        'id': 'r1', 'lab_id': 'lab', 'student_id': None, 'status': 'done', 'error': None, 'model': 'ornith',
        'created_at': '2026-10-15T12:00:00Z', 'finished_at': '2026-10-15T12:00:30Z',
        'facts': {'questions': 108, 'questions_given_to_model': 108, 'pages_matched_by_search': 24,
                  'period': {'first_question': '2026-10-14T08:00:00Z', 'last_question': '2026-10-15T12:00:00Z'}},
        'content': '## Summary\nS1 a posé 108 questions.\n\n## What students asked about\n'
                   '1. **SQL** (~35 questions) : « c\'est quoi la différence entre WHERE et HAVING »\n\n'
                   '## How to prepare the next session\n1. Revoir `CoursInfoDeBase-2022_SQL.pdf` p.28.'}},
}

# role, path, text that shows the page is ready, steps after loading, drawer label
PAGES = [
    ('student', '/chat/lab', 'Quelle différence entre WHERE et HAVING ?', [], 'Classes and sessions'),
    ('teacher', '/teacher', 'Lab Settings', [('tree', 'TP agent')], 'Classes and labs'),
    ('admin', '/admin', 'Model Routing Map', [('nav', 'LLM Config')], 'Menu'),
]


LONG_REPLY = '\n\n'.join(f'Paragraphe {n} : ' + 'une condition sur les groupes. ' * 12 for n in range(1, 31))


def handler(role):
    def api(route):
        path = urlsplit(route.request.url).path
        if path == '/api/auth/me':
            route.fulfill(json={'id': role, 'username': role, 'role': role})
        elif path == '/api/chat/stream':
            route.fulfill(content_type='text/event-stream', body=(
                'event: status\ndata: {"route": "agent", "phase": "thinking", "mode": "research"}\n\n'
                + ''.join(f'event: draft\ndata: {json.dumps(part + chr(10) + chr(10))}\n\n'
                          for part in LONG_REPLY.split('\n\n'))
                + f'data: {json.dumps(LONG_REPLY)}\n\n'
                'event: citations\ndata: []\n\nevent: done\ndata: {}\n\n'))
        else:
            route.fulfill(json=MOCKS.get(path, []))
    return api


def luminance(rgb):
    r, g, b = (int(x) for x in rgb[rgb.index('(') + 1:].split(')')[0].split(',')[:3])
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255


def open_page(browser, target, role, path, theme, size):
    context = browser.new_context(viewport={'width': size[0], 'height': size[1]})
    context.add_init_script(
        "localStorage.setItem('edu_llm_token', 'browser-test');"
        f"if (!localStorage.getItem('edullm-theme')) localStorage.setItem('edullm-theme', '{theme}');")
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    # The app's own API only; /grafana/api/ reaches the real Grafana.
    page.route(lambda url: urlsplit(url).path.startswith('/api/'), handler(role))
    page.goto(target + path)
    return context, page, errors


def check_view(browser, target, shots, role, path, ready, steps, menu, theme, size):
    phone = size == PHONE
    context, page, errors = open_page(browser, target, role, path, theme, size)
    name = f'{role}-{theme}-{size[0]}'
    try:
        for kind, label in steps:
            # 'tree': the teacher drawer starts open; 'nav': phones open the menu first.
            if kind == 'nav' and phone:
                page.get_by_role('button', name=menu).click()
            page.get_by_text(label, exact=True).first.click()
        expect(page.get_by_text(ready, exact=True).first).to_be_visible()

        assert page.evaluate('document.documentElement.dataset.theme') == theme, name
        light = luminance(page.evaluate('getComputedStyle(document.documentElement).backgroundColor'))
        assert (light > 0.6) if theme == 'light' else (light < 0.2), f'{name}: background {light:.2f}'

        overflow = page.evaluate('document.documentElement.scrollWidth - innerWidth')
        assert overflow <= 0, f'{name}: page scrolls sideways by {overflow}px'
        if role == 'admin':
            # The model map itself, with its sources, at every width.
            expect(page.get_by_text('Students', exact=True)).to_be_visible()

        button = page.get_by_role('button', name=menu)
        if phone:
            expect(button).to_be_visible()
            if shots:
                page.screenshot(path=str(shots / f'{name}.png'))
            button.click()
            sign_out = page.get_by_role('button', name='Sign out')
            expect(sign_out).to_be_visible()
            if role == 'student':
                # Drawer order: classes, then sessions, then the account footer.
                tops = [page.get_by_text(t, exact=True).first.bounding_box()['y']
                        for t in ('My Classes', 'Sessions')] + [sign_out.bounding_box()['y']]
                assert tops == sorted(tops), f'{name}: drawer order {tops}'
            if shots:
                page.screenshot(path=str(shots / f'{name}-menu.png'))
        else:
            expect(button).to_be_hidden()
            if shots:
                page.screenshot(path=str(shots / f'{name}.png'))
        if role == 'student':
            # Phones: the drawer is open here. Many sessions must not push the quota away.
            quota = page.get_by_text('Token Quota', exact=True).bounding_box()
            assert quota and quota['y'] + quota['height'] <= size[1], f'{name}: token quota off screen {quota}'
        assert not errors, f'{name}: {errors}'
        print(f'PASS: {name}')
    finally:
        context.close()


def check_long_answer_stays_at_its_start(browser, target):
    """While a long answer is written, the view stops at the answer's first
    line instead of following the newest text to the bottom."""
    for size in (DESKTOP, PHONE):
        context, page, _ = open_page(browser, target, 'student', '/chat/lab', 'light', size)
        try:
            expect(page.get_by_text('Quelle différence entre WHERE et HAVING ?', exact=True)).to_be_visible()
            page.locator('#chat-input').fill('Et GROUP BY ?')
            page.locator('#chat-input').press('Enter')
            expect(page.get_by_text('Paragraphe 30', exact=False)).to_have_count(1)
            page.wait_for_timeout(300)
            start = page.evaluate("""() => {
                const answer = document.querySelector('[data-latest-answer]');
                const list = answer.parentElement;
                return answer.getBoundingClientRect().top - list.getBoundingClientRect().top;
            }""")
            assert 0 <= start <= 40, f'{size[0]}: the answer starts {start:.0f}px from the top of the list'
            expect(page.get_by_text('Paragraphe 1 :', exact=False)).to_be_in_viewport()
            print(f'PASS: a long answer stays at its first line ({size[0]} px)')
        finally:
            context.close()


def check_toggle(browser, target):
    context, page, _ = open_page(browser, target, 'student', '/chat/lab', 'light', DESKTOP)
    try:
        page.get_by_role('button', name='Dark theme').click()
        assert page.evaluate('document.documentElement.dataset.theme') == 'dark'
        page.reload()
        expect(page.get_by_role('button', name='Light theme')).to_be_visible()
        assert page.evaluate('document.documentElement.dataset.theme') == 'dark'
        print('PASS: the theme toggle switches and is remembered after a reload')
    finally:
        context.close()


def check_breadcrumb(browser, target):
    # In a lab, the class name above the tabs opens the class.
    context, page, errors = open_page(browser, target, 'teacher', '/teacher', 'light', DESKTOP)
    try:
        page.get_by_text('TP agent', exact=True).first.click()
        expect(page.get_by_role('button', name='Audit', exact=True)).to_be_visible()
        crumb = page.get_by_role('navigation', name='Breadcrumb').get_by_role('button', name='Informatique')
        crumb.click()
        expect(page.get_by_role('button', name='Audit', exact=True)).to_have_count(0)
        expect(page.get_by_role('navigation', name='Breadcrumb').get_by_text('TP agent')).to_have_count(0)
        assert not errors, errors
        print('PASS: the class name in the breadcrumb opens the class')
    finally:
        context.close()


def check_roles_and_tabs(browser, target):
    # Each role stays in its own workspace, and every tab follows a sign-in or
    # sign-out made in another tab. The token names the role ("as-teacher").
    context = browser.new_context(viewport={'width': DESKTOP[0], 'height': DESKTOP[1]})

    def api(route):
        path = urlsplit(route.request.url).path
        if path == '/api/auth/me':
            role = (route.request.headers.get('authorization') or '').removeprefix('Bearer as-')
            route.fulfill(json={'id': role, 'username': role, 'role': role})
        else:
            route.fulfill(json=MOCKS.get(path, []))

    context.route(lambda url: urlsplit(url).path.startswith('/api/'), api)
    try:
        page = context.new_page()
        page.goto(target + '/login')
        for token, path, lands in (('as-admin', '/teacher', '/admin'), ('as-admin', '/chat/lab', '/admin'),
                                   ('as-teacher', '/chat', '/teacher'), ('as-student', '/teacher', '/chat')):
            page.evaluate(f"localStorage.setItem('edu_llm_token', '{token}')")
            page.goto(target + path)
            expect(page).to_have_url(re.compile(re.escape(lands)))
        print('PASS: an admin or a teacher who opens another workspace goes back to their own')

        page.evaluate("localStorage.setItem('edu_llm_token', 'as-teacher')")
        page.goto(target + '/teacher')
        other = context.new_page()
        other.goto(target + '/login')
        other.evaluate("localStorage.setItem('edu_llm_token', 'as-student')")
        expect(page).to_have_url(re.compile('/chat'))
        expect(page.get_by_text('Signed in as student in another tab.')).to_be_visible()
        other.evaluate("localStorage.removeItem('edu_llm_token')")
        expect(page).to_have_url(re.compile('/login'))
        expect(page.get_by_text('Signed out in another tab.')).to_be_visible()
        print('PASS: a tab follows a sign-in and a sign-out made in another tab')
    finally:
        context.close()


def check_exam_documents(browser, target, shots):
    # Exam mode keeps uploads open and says the documents wait; the note leads to the class settings.
    classes = MOCKS['/api/teacher/classes']
    MOCKS['/api/teacher/classes'] = [{**classes[0], 'exam_mode': True}]
    try:
        for theme, size in (('light', DESKTOP), ('dark', PHONE)):
            name = f'exam-documents-{theme}-{size[0]}'
            context, page, errors = open_page(browser, target, 'teacher', '/teacher', theme, size)
            try:
                page.get_by_text('TP agent', exact=True).first.click()
                page.get_by_role('button', name='Documents', exact=True).click()
                expect(page.get_by_text('Exam mode is on.', exact=True)).to_be_visible()
                expect(page.get_by_role('button', name='Upload', exact=False).first).to_be_enabled()
                overflow = page.evaluate('document.documentElement.scrollWidth - innerWidth')
                assert overflow <= 0, f'{name}: page scrolls sideways by {overflow}px'
                if shots:
                    page.screenshot(path=str(shots / f'{name}.png'), full_page=True)
                page.get_by_role('button', name='Class settings', exact=True).click()
                expect(page.get_by_role('switch', name='Exam mode')).to_be_checked()
                assert not errors, f'{name}: {errors}'
                print(f'PASS: {name}')
            finally:
                context.close()
    finally:
        MOCKS['/api/teacher/classes'] = classes


def reading_doc(name, state, **latest):
    return {'id': name, 'filename': name, 'status': 'pending', 'page_count': None, 'error_message': None,
            'chunk_count': 0, 'lifecycle': 'active', 'generation': 1, 'structured': True,
            'current_revision_id': None, 'scope': 'lab',
            'latest_revision': {'id': f'r-{name}', 'processing_state': state, 'publication_state': 'draft',
                                'parse_quality': None, 'error_code': None, **latest}}


def check_document_progress(browser, target, shots):
    # A document being read shows the jobs ahead, the pages read and the time left, then indexing.
    MOCKS['/api/teacher/labs/lab/documents'] = [
        reading_doc('CM_reseaux_2025_chapitre_3_routage_dynamique.pdf', 'processing',
                    progress={'done': 20, 'total': 40, 'seconds_left': 300}),
        reading_doc('TD4.pdf', 'queued', jobs_ahead=1),
        reading_doc('TP2.pdf', 'parsed'),
    ]
    try:
        for theme, size in (('light', DESKTOP), ('dark', PHONE)):
            name = f'document-progress-{theme}-{size[0]}'
            context, page, errors = open_page(browser, target, 'teacher', '/teacher', theme, size)
            try:
                page.get_by_text('TP agent', exact=True).first.click()
                page.get_by_role('button', name='Documents', exact=True).click()
                expect(page.get_by_text('20 of 40 pages read · about 5 min left', exact=True)).to_be_visible()
                expect(page.get_by_text('Waiting: 1 document ahead', exact=True)).to_be_visible()
                expect(page.get_by_text('Indexing…', exact=True)).to_be_visible()
                expect(page.get_by_text('You can leave this page', exact=False)).to_be_visible()
                bar = page.get_by_role('progressbar').first
                expect(bar).to_have_attribute('aria-valuenow', '48')
                overflow = page.evaluate('document.documentElement.scrollWidth - innerWidth')
                assert overflow <= 0, f'{name}: page scrolls sideways by {overflow}px'
                if shots:
                    page.screenshot(path=str(shots / f'{name}.png'), full_page=True)
                assert not errors, f'{name}: {errors}'
                print(f'PASS: {name}')
            finally:
                context.close()
    finally:
        MOCKS.pop('/api/teacher/labs/lab/documents')


def check_analytics(browser, target, shots):
    # The lab Analytics tab: charts, the student table and a quota refill, at every width.
    for theme in ('light', 'dark'):
        for size in (DESKTOP, PHONE):
            name = f'analytics-{theme}-{size[0]}'
            context, page, errors = open_page(browser, target, 'teacher', '/teacher', theme, size)
            try:
                page.get_by_text('TP agent', exact=True).first.click()
                page.get_by_role('button', name='Analytics', exact=True).click()
                expect(page.get_by_text('When students ask', exact=True)).to_be_visible()
                expect(page.get_by_text('Busiest:', exact=False)).to_be_visible()
                # Each bar and cell shows its number at once, inside the screen (a tap on a phone).
                for cell, text in ((page.get_by_label('Mon 9:00–10:00: 1 question'), 'Mon 9:00–10:00: 1 question'),
                                   (page.locator('[aria-label$=": 4 questions"]').first, ': 4 questions'),
                                   (page.locator('[aria-label^="Sun 22:00"]'), 'Sun 22:00–23:00')):
                    cell.scroll_into_view_if_needed()
                    page.wait_for_timeout(200)       # a scroll of the heatmap closes the tooltip
                    cell.click() if size == PHONE else cell.hover()
                    tip = page.get_by_role('tooltip')
                    expect(tip).to_contain_text(text)
                    box = tip.bounding_box()
                    assert box['x'] >= 0 and box['x'] + box['width'] <= size[0], f'{name}: tooltip off screen {box}'
                overflow = page.evaluate('document.documentElement.scrollWidth - innerWidth')
                assert overflow <= 0, f'{name}: page scrolls sideways by {overflow}px'
                refill = page.get_by_role('button', name='Refill')
                expect(refill).to_have_count(2)          # cat has used nothing: no refill
                if shots:
                    page.screenshot(path=str(shots / f'{name}.png'), full_page=True)
                refill.first.click()
                expect(page.get_by_text('can use 50,000 tokens today', exact=False)).to_be_visible()
                assert not errors, f'{name}: {errors}'
                print(f'PASS: {name}')
            finally:
                context.close()


def check_report(browser, target, shots):
    # Audit > AI report: the latest report, with what it is based on, at every width.
    for theme in ('light', 'dark'):
        for size in (DESKTOP, PHONE):
            name = f'report-{theme}-{size[0]}'
            context, page, errors = open_page(browser, target, 'teacher', '/teacher', theme, size)
            try:
                page.get_by_text('TP agent', exact=True).first.click()
                page.get_by_role('button', name='Audit', exact=True).click()
                page.get_by_role('tab', name='AI report').click()
                expect(page.get_by_role('heading', name='How to prepare the next session')).to_be_visible()
                expect(page.get_by_text('Based on 108 questions', exact=False)).to_be_visible()
                expect(page.get_by_role('button', name='Write again')).to_be_enabled()
                overflow = page.evaluate('document.documentElement.scrollWidth - innerWidth')
                assert overflow <= 0, f'{name}: page scrolls sideways by {overflow}px'
                if shots:
                    page.screenshot(path=str(shots / f'{name}.png'), full_page=True)
                assert not errors, f'{name}: {errors}'
                print(f'PASS: {name}')
            finally:
                context.close()


def check_monitoring(browser, target, shots):
    """Grafana is closed without the monitoring cookie. With MONITORING_COOKIE
    (an admin's monitoring token), the three dashboards load inside the admin page,
    in the app's theme, at both widths."""
    cookie = os.getenv('MONITORING_COOKIE')
    context, page, errors = open_page(browser, target, 'admin', '/admin', 'light', DESKTOP)
    try:
        assert page.request.get(f'{target}/grafana/api/health').status == 401, 'Grafana is open without a login'
        if not cookie:
            # The mocked session sets no cookie: the tab says access is for admins.
            page.get_by_role('button', name='Monitoring').click()
            expect(page.get_by_text('Monitoring is open to admins only', exact=False)).to_be_visible()
            print('PASS: Grafana is closed without an admin (set MONITORING_COOKIE to check the dashboards)')
            return
    finally:
        context.close()
    for theme in ('light', 'dark'):
        for size in (DESKTOP, PHONE):
            name = f'monitoring-{theme}-{size[0]}'
            context, page, errors = open_page(browser, target, 'admin', '/admin', theme, size)
            context.add_cookies([{'name': 'edullm_monitoring', 'value': cookie, 'url': f'{target}/grafana/'}])
            try:
                if size == PHONE:
                    page.get_by_role('button', name='Menu').click()
                page.get_by_role('button', name='Monitoring').click()
                frame = page.frame_locator('iframe[title="system dashboard"]')
                expect(frame.get_by_text('Model servers').first).to_be_visible(timeout=20000)
                body = frame.locator('body')
                light = luminance(body.evaluate('b => getComputedStyle(b).backgroundColor'))
                assert (light > 0.6) if theme == 'light' else (light < 0.2), f'{name}: Grafana background {light:.2f}'
                page.get_by_role('button', name='Answers').click()
                answers = page.frame_locator('iframe[title="answers dashboard"]')
                expect(answers.get_by_text('Students only').first).to_be_visible(timeout=20000)
                page.get_by_role('button', name='Model servers').click()
                models = page.frame_locator('iframe[title="models dashboard"]')
                expect(models.get_by_text('MTP: tokens per step, time range').first).to_be_visible(timeout=20000)
                overflow = page.evaluate('document.documentElement.scrollWidth - innerWidth')
                assert overflow <= 0, f'{name}: page scrolls sideways by {overflow}px'
                if shots:
                    page.wait_for_timeout(2500)  # the panels draw after their queries
                    page.screenshot(path=str(shots / f'{name}.png'))
                assert not errors, f'{name}: {errors}'
                print(f'PASS: {name}')
            finally:
                context.close()


def main():
    target = os.getenv('UI_TEST_URL', 'http://127.0.0.1:8080')
    assert urlsplit(target).hostname == '127.0.0.1', 'Use an explicit loopback frontend'
    shots = Path(os.environ['UI_SHOTS_DIR']) if os.getenv('UI_SHOTS_DIR') else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.getenv('PLAYWRIGHT_CHROMIUM') or None)
        try:
            for role, path, ready, steps, menu in PAGES:
                for theme in ('light', 'dark'):
                    for size in (DESKTOP, PHONE):
                        check_view(browser, target, shots, role, path, ready, steps, menu, theme, size)
            check_toggle(browser, target)
            check_breadcrumb(browser, target)
            check_roles_and_tabs(browser, target)
            check_exam_documents(browser, target, shots)
            check_document_progress(browser, target, shots)
            check_analytics(browser, target, shots)
            check_report(browser, target, shots)
            check_monitoring(browser, target, shots)
            check_long_answer_stays_at_its_start(browser, target)
        finally:
            browser.close()


if __name__ == '__main__':
    main()
