"""Browser regression for the admin config map (requires Playwright + Chromium).

Run against the local demo: python scripts/check_admin_config.py
ADMIN_TEST_URL / ADMIN_TEST_USERNAME / ADMIN_TEST_PASSWORD override the defaults.
"""

import os
import re

from playwright.sync_api import expect, sync_playwright


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(os.getenv("ADMIN_TEST_URL", "http://127.0.0.1") + "/login")
            page.get_by_label("Username", exact=True).fill(os.getenv("ADMIN_TEST_USERNAME", "admin"))
            page.get_by_label("Password", exact=True).fill(os.getenv("ADMIN_TEST_PASSWORD", "admin123"))
            page.get_by_role("button", name="Sign In", exact=True).click()
            page.get_by_role("button", name="LLM Config", exact=True).click()
            expect(page.get_by_role("heading", name="Model Routing Map", exact=True)).to_be_visible()
            for icon, name in [("💬", "Chat LLM"), ("🧭", "Auxiliary"), ("🧲", "Embedding"),
                               ("⚖️", "Reranker"), ("📥", "Ingest model"), ("🧠", "Agent"), ("👁️", "VLM")]:
                page.get_by_role("button", name=re.compile("^" + re.escape(icon + " " + name))).click()
                expect(page.get_by_role("heading", name=icon + " " + name, exact=True)).to_be_visible()
            # Exercise serialization and the save UI without mutating live settings.
            current = page.request.get(os.getenv("ADMIN_TEST_URL", "http://127.0.0.1") + "/api/admin/llm/config", headers={
                "Authorization": "Bearer " + page.evaluate("localStorage.getItem('edu_llm_token')")
            }).json()
            attempts = {"get": 0, "put": 0}

            def config_response(route):
                if route.request.method == "PUT":
                    attempts["put"] += 1
                    assert route.request.post_data_json["model"] == current["model"]
                    route.fulfill(json=current)
                else:
                    attempts["get"] += 1
                    if attempts["get"] == 1:
                        route.fulfill(status=503, json={"detail": "Temporary config test failure"})
                    else:
                        route.continue_()

            page.route("**/api/admin/llm/config", config_response)
            page.get_by_role("button", name="Save VLM", exact=True).click()
            expect(page.get_by_text("VLM saved", exact=True)).to_be_visible()
            page.get_by_role("button", name="Users", exact=True).click()
            page.get_by_role("button", name="LLM Config", exact=True).click()
            expect(page.get_by_role("alert")).to_contain_text("Temporary config test failure")
            page.get_by_role("button", name="Retry loading settings", exact=True).click()
            expect(page.get_by_role("heading", name="Model Routing Map", exact=True)).to_be_visible()
            assert attempts == {"get": 2, "put": 1}
            assert not errors, errors
            print("PASS: admin config renders all nodes, saves, and recovers from a load failure")
        finally:
            if errors:
                print("Browser errors:", errors)
            browser.close()


if __name__ == "__main__":
    main()
