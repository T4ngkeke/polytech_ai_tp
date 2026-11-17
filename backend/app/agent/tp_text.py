"""The text of the TP files for the prompt of Search and Direct.

A chat session belongs to a lab (a TP). The TP's own files (its statement) are
background for every question of the session, not something to search for:
questions such as "what are we doing?" or "question 3?" point at the TP
without describing its content, so a search does not find it. Search and
Direct put the full text of the TP files into the prompt: at the end of the
system message, after the teacher's rules and before the conversation. The
limit (max_chars) is a share of the model's context (TP_CONTEXT_PERCENT, an
admin setting, 30% by default; tp_char_budget). Files go in whole, the
shortest first (a TP statement is usually shorter than course slides), as long
as they fit; a file that does not fit is left out and found by the search as
before. The teacher's page warns when TP files are left out. It is the same text for every question of
the session, so the model server can reuse its computation (prefix cache).

Each page has a header with its file name and page number, as a page found by
Search has. With cite=True (Search) each line starts with its evidence ID, so
the model cites a TP line as it cites a found page. These IDs are the same in
every run of the session: the TP is read before anything else and numbered
from E1, and the other pages of a run are numbered after it. When an earlier
answer in the history used one of these IDs for another source (a session
from before this change, or a Research answer), the TP is numbered after the
session's IDs instead, as every page was before.

Only files of the run's scope are read: the published files of the lab and of
the class that the student may read (in exam mode, only the allowed files).

page_text renders one page read for the prompt; Search uses it for the pages
it finds too.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import UUID

from backend.app.agent.course_tools import _page_furniture
from backend.app.agent.evidence import EvidenceStore
from backend.app.services import failure_log

PAGE_CHARS = 6000      # text of one page
TP_CHARS = 36000       # default limit of the TP text (about 12k tokens); see tp_char_budget
TP_MAX_PAGES = 120     # pages read at most: the files after them are left out unread
CHARS_PER_TOKEN = 3    # as history.estimate_tokens
DEFAULT_CONTEXT = 65536


def tp_char_budget(context_tokens: int | None, percent: int) -> int:
    """The TP text limit in characters: percent of the model's context
    (DEFAULT_CONTEXT when the server does not say). 0: no TP text."""
    return int((context_tokens or DEFAULT_CONTEXT) * percent / 100) * CHARS_PER_TOKEN

THIS_TP = '[This TP] '
CLASS_COURSE = '[Class course] '
UNREADABLE = '(This page could not be read: this part of the TP statement is missing.)'
NO_TEXT = '(No text was found on this page: a figure or a table on it may be missing here.)'


def page_text(read: dict, *, ids: bool = True) -> str:
    """A page as the model sees it: one line per block, with its evidence ID
    (ids=False: without). Footer numbers are left out; picture descriptions
    are added to their block."""
    if not read.get('ok'):
        return ''
    evidence_ids = read.get('item_evidence_ids') or []
    lines = []
    for number, item in enumerate(read.get('items') or []):
        if _page_furniture(item):
            continue
        content = (item.get('content') or '').strip()
        if item.get('content_description'):
            note = 'unchecked model ' if item.get('description_checked') is False else ''
            content += f' ({note}description of the {item.get("type") or "block"}: {item["content_description"]})'
        if content:
            evidence_id = evidence_ids[number] if ids and number < len(evidence_ids) else None
            lines.append(f'[{evidence_id}] {content}' if evidence_id else content)
    text = '\n'.join(lines)
    if len(text) > PAGE_CHARS or read.get('next_cursor'):
        text = text[:PAGE_CHARS] + '\n(page text shortened)'
    return text


def page_header(view, document_id: str, page_id: str, source: str = '') -> str:
    """'--- [This TP] TP1.pdf, page 3 ---' (source: THIS_TP, CLASS_COURSE or '')."""
    page_no = view.page_handles.get(page_id, '').rsplit('.p', 1)[-1]
    return f'--- {source}{view.filenames.get(document_id, "document")}, page {page_no} ---'


def plan_files(files: list[tuple[str, int, int, str]], max_chars: int,
               max_pages: int = TP_MAX_PAGES) -> list[str]:
    """The files that go in whole: files are (id, pages, characters, filename);
    the shortest first (pages, then name), at most max_pages pages read, while
    the characters fit. The chat (tp_text, with the real text) and the
    teacher's Documents tab (estimated sizes) both decide with it."""
    chosen, pages, chars = [], 0, 0
    for doc, n_pages, n_chars, _name in sorted(files, key=lambda f: (f[1], f[3])):
        if pages + n_pages > max_pages:
            continue
        pages += n_pages
        if chars + n_chars <= max_chars:
            chosen.append(doc)
            chars += n_chars
    return chosen


def page_overhead(filename: str, pages: int, blocks: int, cite: bool = True) -> int:
    """Characters the prompt adds to a file's text: a header per page and,
    with cite, an evidence ID per line."""
    return pages * (len('--- [This TP] , page 00 ---\n\n') + len(filename)) + (blocks * 7 if cite else 0)


@dataclass
class TPText:
    documents: list[str]  # the TP files in the text (document IDs)
    text: str             # their pages with headers; '' when no file fits
    pages: int            # pages in the text
    chars: int            # size of the text
    left_out: list[str] = field(default_factory=list)  # TP files too long for the limit


def _same_source(evidence, citation: dict) -> bool:
    mine = evidence.citation()
    return (all(str(mine[key]) == str(citation.get(key))
                for key in ('document_id', 'revision_id', 'page_id', 'block_id'))
            and mine['range'] == citation.get('range'))


def _ids_taken(store: EvidenceStore, earlier_citations: dict | None) -> bool:
    """Whether an earlier answer of the history cites one of the TP's IDs for
    another source."""
    for label, citation in (earlier_citations or {}).items():
        if int(label[1:]) < store.next_number:
            evidence = store.get(label)
            if evidence is None or not _same_source(evidence, citation):
                return True
    return False


async def tp_text(course_tools, *, cite: bool, earlier_citations: dict | None = None,
                  max_chars: int | None = None) -> TPText | None:
    """The TP files of the run as one text, or None when the run has no TP file.

    course_tools: the run's CourseTools. cite: give each line its evidence ID
    (Search). max_chars: the limit (tp_char_budget; None: TP_CHARS). Read
    before any other page of the run. The reads do not use the run's tool budget."""
    max_chars = TP_CHARS if max_chars is None else max_chars
    await course_tools.document_count()
    view = course_tools.view
    documents = [d for d in view.documents.values() if view.lab_names.get(d)]
    if not documents:
        return None
    pages_of = {d: [view.pages[h] for h in view.doc_pages[view.doc_handles[d]]] for d in documents}
    # The files past TP_MAX_PAGES are not read (plan_files with no size yet).
    readable = plan_files([(d, len(pages_of[d]), 0, view.filenames.get(d, '')) for d in documents], 1)
    pages = [page for d in readable for page in pages_of[d]]
    session_first = course_tools.evidence.next_number
    # Stable IDs only when nothing has been read yet in this run.
    stable = cite and not len(course_tools.evidence)
    if stable:
        course_tools.evidence.next_number = 1
    course_tools.max_tool_calls += len(pages)
    reads = dict(zip(pages, await asyncio.gather(*(course_tools.execute('read', {
        'document_id': document_id, 'page_id': page_id, 'budget': PAGE_CHARS})
        for document_id, page_id in pages)))) if pages and max_chars > 0 else {}

    def render(doc):
        blocks = []
        for document_id, page_id in pages_of[doc]:
            read = reads[(document_id, page_id)]
            text = page_text(read, ids=cite) if read.get('ok') else UNREADABLE
            blocks.append(page_header(view, document_id, page_id, THIS_TP) + '\n' + (text or NO_TEXT))
        return '\n\n'.join(blocks)

    # Whole files, shortest first, while they fit.
    chosen = plan_files([(d, len(pages_of[d]), len(render(d)) + 2, view.filenames.get(d, ''))
                         for d in readable], max_chars) if reads else []
    for (document_id, page_id), read in reads.items():
        if document_id in chosen and not read.get('ok'):
            failure_log.record('tp_text', str((read.get('error') or {}).get('code') or 'unreadable'))
    complete = len(chosen) == len(readable) == len(documents)
    if stable and (not complete or _ids_taken(course_tools.evidence, earlier_citations)):
        # Only the files in the text keep IDs. From E1 when no earlier answer
        # uses these numbers for another source, else after the session's IDs
        # as before. The left-out files are found by the search again.
        taken = _ids_taken(course_tools.evidence, earlier_citations)
        course_tools.evidence = EvidenceStore(course_tools.scope.snapshot_revisions,
                                              first_number=session_first if (taken or not chosen) else 1)
        for document_id, page_id in [page for d in chosen for page in pages_of[d]]:
            read = reads[(document_id, page_id)]
            if read.get('ok'):
                reads[(document_id, page_id)] = {**read, 'item_evidence_ids': course_tools._ids(
                    'read', read, UUID(document_id))}
        if not taken and chosen:
            course_tools.evidence.next_number = max(course_tools.evidence.next_number, session_first)
    elif stable:
        # The other pages of the run come after the TP and after every ID the
        # session's answers cite.
        course_tools.evidence.next_number = max(course_tools.evidence.next_number, session_first)
    # In the order of the lab's file list, as the catalogue shows them.
    in_text = [d for d in documents if d in chosen]
    text = '\n\n'.join(render(d) for d in in_text)
    return TPText(in_text, text, sum(len(pages_of[d]) for d in in_text), len(text),
                  [d for d in documents if d not in chosen])
