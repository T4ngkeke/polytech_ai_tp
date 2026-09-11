"""Mocked API browser check against an isolated frontend; no DB/model writes."""
import os
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright,expect

with sync_playwright() as p:
    browser=p.chromium.launch()
    page=browser.new_page()
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.add_init_script("localStorage.setItem('edu_llm_token','test')")
    def route_api(route):
        path=urlsplit(route.request.url).path
        if path=='/api/auth/me': value={'id':'t','username':'teacher','role':'teacher'}
        elif path=='/api/teacher/classes': value=[{'id':'c','name':'Test class'}]
        elif path.endswith('/classes/c/labs'): value=[{'id':'l','name':'Test lab','is_active':True}]
        elif path.endswith('/labs/l/documents'): value=[
            {'id':'d','filename':'partial.pdf','status':'partially_indexed','page_count':3,'chunk_count':2,'ingest_report':{'page_summary':{'total':3,'updated':[1],'retained':[2],'missing':[3]},'page_reader':{'page_results':[{'page_no':2,'status':'retained','error':'Teacher changed this page'},{'page_no':3,'status':'needs_review','error':'Formula unreadable'}]}}},
            {'id':'e','filename':'scanned.pdf','status':'partially_indexed','page_count':3,'chunk_count':2,'ingest_report':{
                'index_state':{'status':'partially_indexed','page_count':3,'available_pages':[1,2],'missing_pages':[],'no_index_pages':[3],'pending_review_count':0,'retained_pages':[2],'problem_pages':[2]},
                'page_summary':{'total':3,'updated':[1],'retained':[2],'missing':[3]},'page_reader':{'page_results':[{'page_no':2,'status':'retained','error':'review_source_conflict: overlap'}]}}}]
        else:value=[]
        route.fulfill(json=value)
    page.route('**/api/**',route_api)
    page.goto(os.getenv('PARTIAL_UI_URL','http://127.0.0.1:18080')+'/teacher')
    page.get_by_text('Test class',exact=True).click()
    page.get_by_text('Test lab',exact=True).click()
    page.get_by_role('button',name='Documents',exact=True).click()
    expect(page.get_by_text('partially indexed',exact=True).first).to_be_visible()
    page.get_by_text('partial.pdf',exact=True).click()
    expect(page.get_by_text('3 pages: 1 updated, 1 kept from the previous run, 1 unavailable.',exact=False)).to_be_visible()
    expect(page.get_by_text('Page 3: needs_review. Formula unreadable',exact=False)).to_be_visible()
    # Current state: a "no indexing needed" page is not an unavailable page; history stays visible.
    page.get_by_role('button',name='← Back to documents').click()
    expect(page.get_by_text('2 usable · 0 unavailable · 1 not indexed',exact=True)).to_be_visible()
    page.get_by_text('scanned.pdf',exact=True).click()
    expect(page.get_by_text('Now: 2 pages usable, 0 unavailable, 1 marked "no indexing needed", 0 visual reviews pending.',exact=False)).to_be_visible()
    expect(page.get_by_text('Last processing run, 3 pages: 1 updated, 1 kept from the previous run, 1 unavailable.',exact=False)).to_be_visible()
    expect(page.get_by_text('Page 2: retained. review_source_conflict: overlap',exact=False)).to_be_visible()
    assert not errors, errors
    print('PASS: partial status, counts, missing page, reason, current state and no-index pages shown')
    browser.close()
