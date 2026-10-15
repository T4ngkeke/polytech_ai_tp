"""Browser check of the light/dark themes and the phone layout; all API traffic is mocked.

Each main page (student chat, teacher lab, admin model map) is opened in both
themes at desktop (1440 px) and phone (390 px) width. The check fails when the
page scrolls sideways, the theme is not applied, the phone menu does not open
the navigation drawer, the student drawer is not ordered classes, sessions,
account, the token quota is pushed off screen, or the admin model map is missing.

UI_TEST_URL must be a loopback frontend; it defaults to v9's mapped port 8080.
PLAYWRIGHT_CHROMIUM may name a Chromium executable when Playwright's own build
is not installed. UI_SHOTS_DIR, when set, receives a screenshot of each view
for a human look.
"""
import os
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
}

# role, path, text that shows the page is ready, steps after loading, drawer label
PAGES = [
    ('student', '/chat/lab', 'Quelle différence entre WHERE et HAVING ?', [], 'Classes and sessions'),
    ('teacher', '/teacher', 'Lab Settings', [('tree', 'TP agent')], 'Classes and labs'),
    ('admin', '/admin', 'Model Routing Map', [('nav', 'LLM Config')], 'Menu'),
]


def handler(role):
    def api(route):
        path = urlsplit(route.request.url).path
        if path == '/api/auth/me':
            route.fulfill(json={'id': role, 'username': role, 'role': role})
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
    page.route('**/api/**', handler(role))
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
        finally:
            browser.close()


if __name__ == '__main__':
    main()
