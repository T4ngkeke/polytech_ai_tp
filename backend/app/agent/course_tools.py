"""Course tools as the research model sees and uses them.

ResearchTools (tools.py) authorizes and reads; its results are full reader
envelopes, built for the API, the review UI and citation audit. A model needs
much less: the text, its evidence ID, the page, and where it can go next.

CourseTools is the one object a research run talks to:
  - ToolView gives the model short run-local handles (d1, d1.p20, b3, a2, c1)
    instead of UUIDs and opaque cursors, translates them back before a tool
    runs, and renders each result as compact text;
  - CourseTools.call enforces the run's tool budget, registers verified
    evidence (E1, E2, ...) from read and view_image, and keeps a trace with
    the complete envelope. Evidence and citations are built from that trace,
    never from the rendered text.

Handles:
  d1        a document of the scope, ordered by filename
  d1.p20    PDF page 20 of d1 (d1.o5 for a page without a verified PDF number)
  d1.p20.b3 a block seen in an earlier result (b3 and d1.b3 are accepted too)
  a2        an image: a figure asset or a page preview
  c1        a continuation of an earlier open/read/grep
"""
from __future__ import annotations

import re
import time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, ValidationError, model_validator

from backend.app.agent.evidence import EvidenceStore
from backend.app.agent.result import ResearchQuotaExceeded, ResearchRunStopped
from backend.app.agent.tools import Kind


class ViewArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SearchView(ViewArgs):
    query: StrictStr = Field(min_length=1, max_length=500)
    document: StrictStr | None = Field(default=None, description='Optional document handle, e.g. d1.')
    kind: Kind | None = None


class OpenView(ViewArgs):
    document: StrictStr | None = Field(default=None, description='Document handle, e.g. d1.')
    page: StrictStr | None = Field(default=None, description='Start at this page handle, e.g. d1.p20.')
    cursor: StrictStr | None = Field(default=None, description='Continuation handle, e.g. c1.')

    @model_validator(mode='after')
    def one_start(self):
        if self.cursor and (self.document or self.page):
            raise ValueError('give either cursor alone, or document and/or page')
        if not (self.cursor or self.document or self.page):
            raise ValueError('give a document, a page or a cursor')
        return self


class NavigateView(ViewArgs):
    block: StrictStr = Field(description='Block handle to start from, e.g. d1.p20.b3.')
    direction: Literal['next', 'previous']
    unit: Literal['block', 'page'] = 'block'
    count: int = Field(default=1, ge=1, le=3, strict=True)


class ReadView(ViewArgs):
    page: StrictStr | None = Field(default=None, description='Page handle, e.g. d1.p20.')
    block: StrictStr | None = Field(default=None, description='Block handle, e.g. d1.p20.b3.')
    start: int | None = Field(default=None, ge=0, strict=True, description='Block reads only.')
    end: int | None = Field(default=None, ge=0, strict=True, description='Block reads only.')
    cursor: StrictStr | None = Field(default=None, description='Continuation handle, e.g. c1.')

    @model_validator(mode='after')
    def one_target(self):
        if sum(x is not None for x in (self.page, self.block, self.cursor)) != 1:
            raise ValueError('give exactly one of page, block or cursor; '
                             'a block on a page is written block="d1.p20.b3"')
        if (self.start is not None or self.end is not None) and self.block is None:
            raise ValueError('start and end only work with block')
        return self


class GrepView(ViewArgs):
    document: StrictStr | None = Field(default=None, description='Document handle, e.g. d1.')
    pattern: StrictStr | None = Field(default=None, min_length=1, max_length=200)
    case_sensitive: StrictBool = False
    cursor: StrictStr | None = Field(default=None, description='Continuation handle, e.g. c1.')

    @model_validator(mode='after')
    def query_or_cursor(self):
        if self.cursor and (self.document or self.pattern):
            raise ValueError('give either cursor alone, or document and pattern')
        if not self.cursor and not (self.document and self.pattern):
            raise ValueError('give document and pattern')
        return self


class ViewImageView(ViewArgs):
    image: StrictStr = Field(description='Image handle, e.g. a2.')
    question: StrictStr = Field(min_length=1, max_length=500)


VIEW_SCHEMAS: dict[str, type[ViewArgs]] = {
    'search': SearchView, 'open': OpenView, 'navigate': NavigateView,
    'read': ReadView, 'grep': GrepView, 'view_image': ViewImageView,
}

DESCRIPTIONS = {
    'search': 'Find relevant course PDF locations using French course terms. Results are leads, not verified evidence. Read promising pages or blocks before citing them. Reformulate if necessary.',
    'open': 'List the pages of a course PDF with a short summary each, from its start or from a page handle. Use the returned cursor to continue.',
    'navigate': 'List the blocks or pages before or after a known block. Each page read already names its previous and next page.',
    'read': 'Read verified course text of one page (page="d1.p20") or one block (block="d1.p20.b3"), or continue with a cursor (cursor="c1"); give exactly one of them. start/end apply only to block reads. Each returned line starts with its evidence ID, cite it as [E1]. A read can be incomplete; follow the cursor it names.',
    'grep': 'Find a literal pattern in one course document; returns blocks to read. Matches are leads; read them before citing.',
    'view_image': 'Ask a vision model a narrow question about an image handle (a figure or a page image). The answer is visual evidence with uncertainties, not native PDF text.',
}


class HandleError(ValueError):
    pass


# A block is shown as d4.p9.b4. Models also write b4, d4.b4 or "d4.p9 b4";
# every part that is given must match where the block was shown.
LOCATION = re.compile(r'(?:(?P<doc>d\d+)(?:\.(?P<page>[pox]\d+))?)?'
                      r'(?:\s*\.?\s*(?:block\s*)?(?P<block>b\d+))?')


def _page_furniture(item) -> bool:
    """A footer number or logo residue. Ingestion also files some real
    annotations as navigation (SQL p.21), so only very short ones are hidden."""
    text = (item.get('content') or '').strip()
    return item.get('type') == 'navigation' and (len(text) <= 3 or text.isdigit())


def error_text(result: dict) -> str:
    """A failed tool call as the model sees it: 'error: code (message)' and a hint."""
    error = result.get('error') or {}
    text = 'error: ' + str(error.get('code') or 'tool_failed')
    if error.get('message') and error.get('message') != error.get('code'):
        text += f' ({error["message"]})'
    if result.get('hint'):
        text += '\n' + result['hint']
    return text


class ToolView:
    """Run-local handles and the compact rendering of one run's results."""

    def __init__(self, index: dict):
        # index: {document_id: {'filename': str, 'pages': [{'page_id', 'pdf_page_no'}, ...]}}
        self.documents: dict[str, str] = {}          # d1 -> document_id
        self.filenames: dict[str, str] = {}          # document_id -> filename
        self.pages: dict[str, tuple[str, str]] = {}  # d1.p20 -> (document_id, page_id)
        self.page_handles: dict[str, str] = {}       # page_id -> d1.p20
        self.neighbours: dict[str, tuple[str | None, str | None, int, int]] = {}
        for number, (document_id, entry) in enumerate(
                sorted(index.items(), key=lambda kv: (kv[1]['filename'], kv[0])), 1):
            doc = f'd{number}'
            self.documents[doc] = document_id
            self.filenames[document_id] = entry['filename']
            handles = []
            for position, page in enumerate(entry['pages'], 1):
                handle = f'{doc}.p{page["pdf_page_no"]}' if page.get('pdf_page_no') else f'{doc}.o{position}'
                self.pages[handle] = (document_id, page['page_id'])
                self.page_handles[page['page_id']] = handle
                handles.append(handle)
            for position, handle in enumerate(handles):
                self.neighbours[handle] = (handles[position - 1] if position else None,
                                           handles[position + 1] if position + 1 < len(handles) else None,
                                           position + 1, len(handles))
        self.doc_handles = {v: k for k, v in self.documents.items()}
        self.blocks: dict[str, tuple[str, str]] = {}   # b3 -> (document_id, block_id)
        self.block_handles: dict[str, str] = {}
        self.block_pages: dict[str, str] = {}          # b3 -> d1.p20, where it was shown
        self.images: dict[str, tuple[str, str | None]] = {}  # a2 -> (asset_id, page_id for a preview)
        self.image_handles: dict[tuple[str, str | None], str] = {}
        self.cursors: dict[str, tuple[str, dict]] = {}  # c1 -> (tool, raw arguments with cursor)

    # -- handles -------------------------------------------------------------

    def _doc(self, handle):
        if handle not in self.documents:
            raise HandleError(f'unknown document {handle!r}; use one of {", ".join(self.documents) or "none"}')
        return self.documents[handle]

    def _location(self, handle):
        match = LOCATION.fullmatch(handle.strip())
        if not match:
            return None, None, None
        page = f'{match["doc"]}.{match["page"]}' if match['page'] else None
        return match['doc'], page, match['block']

    def _page(self, handle):
        _, page, block = self._location(handle)
        if page in self.pages and (block is None or self.block_pages.get(block) == page):
            return self.pages[page]
        raise HandleError(f'unknown page {handle!r}; page handles look like d1.p20')

    def _block(self, handle):
        doc, page, block = self._location(handle)
        if (block in self.blocks and (doc is None or self.documents.get(doc) == self.blocks[block][0])
                and (page is None or self.block_pages.get(block) == page)):
            return self.blocks[block]
        raise HandleError(f'unknown block {handle!r}; use a block handle such as d1.p20.b3 from an earlier result')

    def block(self, document_id, block_id, page_id=None) -> str:
        key = (str(document_id), str(block_id))
        if key[1] not in self.block_handles:
            handle = f'b{len(self.blocks) + 1}'
            self.blocks[handle] = key
            self.block_handles[key[1]] = handle
        handle = self.block_handles[key[1]]
        if page_id:
            self.block_pages[handle] = self.page(page_id, document_id)
        return f'{self.block_pages[handle]}.{handle}' if handle in self.block_pages else handle

    def image(self, asset_id, page_id=None) -> str:
        key = (str(asset_id), str(page_id) if page_id else None)
        if key not in self.image_handles:
            handle = f'a{len(self.images) + 1}'
            self.images[handle] = key
            self.image_handles[key] = handle
        return self.image_handles[key]

    def page(self, page_id, document_id=None) -> str:
        page_id = str(page_id)
        if page_id not in self.page_handles:
            # Not in the index (should not happen for a frozen scope); keep it addressable.
            doc = self.doc_handles.get(str(document_id), 'd?')
            handle = f'{doc}.x{len(self.pages) + 1}'
            self.pages[handle] = (str(document_id), page_id)
            self.page_handles[page_id] = handle
        return self.page_handles[page_id]

    def _cursor(self, tool, raw_args, cursor) -> str | None:
        if not cursor:
            return None
        handle = f'c{len(self.cursors) + 1}'
        # A read cursor stays bound to its page or block; an open cursor carries
        # its position itself and refuses a second anchor.
        dropped = ('cursor', 'start', 'end') + (('page_id', 'block_id') if tool == 'open' else ())
        self.cursors[handle] = (tool, {**{k: v for k, v in raw_args.items() if k not in dropped},
                                       'cursor': cursor})
        return handle

    def translate(self, name: str, arguments: dict) -> dict:
        """Model arguments with handles -> ResearchTools arguments. Raises HandleError."""
        schema = VIEW_SCHEMAS.get(name)
        if schema is None:
            raise HandleError(f'unknown tool {name!r}')
        try:
            args = schema.model_validate(arguments or {})
        except ValidationError as exc:
            problems = '; '.join(f'{".".join(map(str, e["loc"])) or "arguments"}: {e["msg"]}'
                                 for e in exc.errors(include_url=False))
            raise HandleError(problems) from None
        if getattr(args, 'cursor', None):
            if args.cursor not in self.cursors or self.cursors[args.cursor][0] != name:
                raise HandleError(f'unknown {name} cursor {args.cursor!r}')
            return dict(self.cursors[args.cursor][1])
        if name == 'search':
            raw = {'query': args.query}
            if args.document:
                raw['document_id'] = self._doc(args.document)
            if args.kind:
                raw['kind'] = args.kind
            return raw
        if name == 'open':
            if args.page:
                document_id, page_id = self._page(args.page)
                if args.document and self._doc(args.document) != document_id:
                    raise HandleError(f'page {args.page!r} is not in {args.document!r}')
                return {'document_id': document_id, 'page_id': page_id}
            return {'document_id': self._doc(args.document)}
        if name == 'navigate':
            document_id, block_id = self._block(args.block)
            return {'document_id': document_id, 'anchor_block_id': block_id,
                    'direction': args.direction, 'unit': args.unit, 'count': args.count}
        if name == 'read':
            if args.page:
                document_id, page_id = self._page(args.page)
                return {'document_id': document_id, 'page_id': page_id}
            document_id, block_id = self._block(args.block)
            raw = {'document_id': document_id, 'block_id': block_id}
            if args.start is not None:
                raw['start'] = args.start
            if args.end is not None:
                raw['end'] = args.end
            return raw
        if name == 'grep':
            return {'document_id': self._doc(args.document), 'pattern': args.pattern,
                    'case_sensitive': args.case_sensitive}
        if args.image not in self.images:
            raise HandleError(f'unknown image {args.image!r}; use an image handle from an earlier result')
        asset_id, page_id = self.images[args.image]
        raw = {'asset_id': asset_id, 'question': args.question}
        if page_id:
            raw['page_id'] = page_id
        return raw

    # -- rendering -----------------------------------------------------------

    def _page_header(self, document_id, page_ref) -> str:
        handle = self.page(page_ref['page_id'], document_id)
        previous, following, position, count = self.neighbours.get(handle, (None, None, 0, 0))
        parts = [handle, self.filenames.get(str(document_id), 'unknown document')]
        if count:
            parts.append(f'page {position} of {count}')
        parts.append(f'previous {previous}' if previous else 'first page')
        parts.append(f'next {following}' if following else 'last page')
        if page_ref.get('preview_asset_id'):
            parts.append('page image ' + self.image(page_ref['preview_asset_id'], page_ref['page_id']))
        return ' · '.join(parts)

    def _legend(self, document_ids) -> str:
        seen = []
        for document_id in document_ids:
            if document_id not in seen:
                seen.append(document_id)
        return 'documents: ' + ', '.join(
            f'{self.doc_handles.get(d, "d?")} = {self.filenames.get(d, "unknown")}' for d in seen)

    @staticmethod
    def _issue_codes(issues) -> str:
        return ', '.join(str(i.get('code') or i.get('type') or i) if isinstance(i, dict) else str(i)
                         for i in issues or [])

    def _block_line(self, document_id, item, evidence_id, page_image=None) -> str:
        handle = self.block(document_id, item['block_id'], (item.get('page') or {}).get('page_id'))
        label = f'[{evidence_id}] ' if evidence_id else '[no evidence] '
        line = f'{label}{handle} {item.get("type") or "block"}'
        images = [self.image(a['asset_id']) for a in item.get('assets') or [] if a.get('asset_id')]
        if images:
            line += ' (image ' + ', '.join(images) + ')'
        start, end, length = (item.get('range') or {}).get('start'), (item.get('range') or {}).get('end'), \
            item.get('block_length')
        if item.get('returned_complete_block') is False and length is not None:
            line += f' (characters {start}-{end} of {length})'
        content = item.get('content') or ''
        line += ': ' + (content if content.strip() else '(no text)')
        if item.get('content_description'):
            line += f'\n    description by {item.get("description_origin") or "unknown"}: {item["content_description"]}'
        if item.get('source_completeness') not in (None, 'complete'):
            line += (f'\n    source {item["source_completeness"]}: the text may miss content; '
                     f'check the page image{" " + page_image if page_image else ""} with view_image')
        if item.get('issues'):
            line += '\n    issues: ' + self._issue_codes(item['issues'])
        return line

    def render(self, name: str, raw_args: dict, result: dict) -> str:
        if not result.get('ok'):
            return error_text(result)
        render = getattr(self, '_render_' + name)
        lines = render(raw_args, result)
        notes = [str(i) for i in result.get('issues') or [] if isinstance(i, str)]
        if notes:
            lines.append('notes: ' + ', '.join(dict.fromkeys(notes)))
        return '\n'.join(lines)

    def _render_search(self, raw_args, result):
        items = result.get('items') or []
        if not items:
            return ['no matching course location' + (' (all results filtered)' if result.get('all_filtered') else '')
                    + '; reformulate or inspect nearby pages']
        lines = [f'{len(items)} locations (leads, not evidence; read a page or block before citing)',
                 self._legend([i['document_id'] for i in items])]
        for item in items:
            text = ' … '.join(s.get('content', '') for s in item.get('snippets') or [])
            line = (f'{self.block(item["document_id"], item["block_id"], item["page_id"])} '
                    f'{item.get("type") or "block"}: {text}')
            if item.get('source_completeness') not in (None, 'complete'):
                line += f' [source {item["source_completeness"]}]'
            lines.append(line)
        if result.get('truncated'):
            lines.append('more results exist; reformulate to see others')
        return lines

    def _evidence_for(self, result, index):
        ids = result.get('item_evidence_ids')
        return ids[index] if ids and index < len(ids) else None

    def _render_read(self, raw_args, result):
        document_id = raw_args['document_id']
        items = result.get('items') or []
        page_ref = result.get('page') or (items[0].get('page') if items else None)
        lines = [self._page_header(document_id, page_ref)] if page_ref else []
        shown = set()
        for number, item in enumerate(items):
            if _page_furniture(item):
                continue
            if page_ref and item.get('page') and item['page'].get('page_id') != page_ref.get('page_id'):
                lines.append(self._page_header(document_id, item['page']))
                page_ref = item['page']
            preview = page_ref and page_ref.get('preview_asset_id')
            lines.append(self._block_line(document_id, item, self._evidence_for(result, number),
                                          self.image(preview, page_ref['page_id']) if preview else None))
            shown.add(item.get('block_id'))
        # Incomplete blocks already shown carry their own note; list only the others.
        for missing in result.get('unavailable') or []:
            if missing.get('scope') == 'block':
                if missing.get('block_id') in shown or missing.get('type') == 'navigation':
                    continue
                lines.append(f'not returned: {self.block(document_id, missing["block_id"])} {missing.get("type")} '
                             f'(source {missing.get("code")}); check the page image with view_image')
            else:
                lines.append(f'page issue: {missing.get("code")} {missing.get("message") or ""}'.rstrip())
        cursor = self._cursor('read', raw_args, result.get('next_cursor'))
        if cursor:
            lines.append(f'incomplete: continue with read(cursor="{cursor}")')
        return lines

    def _render_open(self, raw_args, result):
        document_id = result.get('document_id') or raw_args['document_id']
        items = result.get('items') or []
        lines = [f'pages of {self.doc_handles.get(str(document_id), "d?")} '
                 f'({self.filenames.get(str(document_id), "unknown")}, {result.get("page_count")} pages)']
        for item in items:
            line = self.page(item['page_id'], document_id)
            if item.get('title'):
                line += f' "{item["title"]}"'
            lines.append(f'{line}: {item.get("summary") or "(no text)"}')
        cursor = self._cursor('open', raw_args, result.get('next_cursor'))
        if cursor:
            lines.append(f'more pages: open(cursor="{cursor}")')
        return lines

    def _render_navigate(self, raw_args, result):
        document_id = raw_args['document_id']
        lines = []
        for item in result.get('items') or []:
            if 'block_id' in item:
                lines.append(f'{self.block(document_id, item["block_id"], item["page"]["page_id"])} '
                             f'{item.get("type") or "block"}: {item.get("summary") or "(no text)"}')
            else:
                line = self.page(item['page_id'], document_id)
                if item.get('title'):
                    line += f' "{item["title"]}"'
                lines.append(f'{line}: {item.get("summary") or "(no text)"}')
        if result.get('at_start'):
            lines.append('start of document reached')
        if result.get('at_end'):
            lines.append('end of document reached')
        return lines or ['nothing in that direction']

    def _render_grep(self, raw_args, result):
        document_id = raw_args['document_id']
        items = result.get('items') or []
        lines = [f'{len(items)} matches (leads; read them before citing)']
        for item in items:
            lines.append(f'{self.block(document_id, item["block_id"], item["page"]["page_id"])} '
                         f'{item.get("type") or "block"}: …{item.get("context") or ""}…')
        cursor = self._cursor('grep', raw_args, result.get('next_cursor'))
        if cursor:
            lines.append(f'more matches: grep(cursor="{cursor}")')
        return lines

    def _render_view_image(self, raw_args, result):
        lines = []
        for number, item in enumerate(result.get('items') or []):
            evidence_id = self._evidence_for(result, number)
            label = f'[{evidence_id}] ' if evidence_id else '[no evidence] '
            where = self.page(item['page_id'], item.get('document_id'))
            lines.append(f'{label}visual observation of {self.image(item["asset_id"], raw_args.get("page_id"))} '
                         f'on {where} (written by a vision model, not PDF text): {item.get("content") or ""}')
            if item.get('uncertainties'):
                lines.append('    uncertain: ' + '; '.join(map(str, item['uncertainties'])))
        return lines


class CourseTools:
    """The course tools of one research run, with handles, budget and evidence."""

    def __init__(self, tools, *, max_tool_calls=8, research_seconds=45.0, max_visual_calls=2,
                 first_evidence=1):
        # tools: ResearchTools (or a test double with the same methods).
        self.tools, self.scope = tools, tools.scope
        self.max_tool_calls, self.research_seconds = max_tool_calls, research_seconds
        self.max_visual_calls = max_visual_calls
        self.evidence = EvidenceStore(self.scope.snapshot_revisions, first_number=first_evidence)
        self.trace: list[dict] = []
        self.started = time.monotonic()
        self.visual_calls = 0
        self.view: ToolView | None = None

    async def _revision(self, document_id):
        return await self.tools._revision(document_id)

    async def _check_session(self):
        return await self.tools._check_session()

    def state(self):
        """The run's tool record: trace, evidence and handles (JSON-ready)."""
        return {'run_id': str(self.scope.run_id), 'tool_calls': len(self.trace), 'trace': self.trace,
                'evidence': [{**e.citation(), 'content': e.content} for e in self.evidence.all()],
                'handles': self.view and {
                    'documents': self.view.documents, 'filenames': self.view.filenames,
                    'blocks': self.view.blocks,
                    'images': self.view.images,
                    'cursors': {h: tool for h, (tool, _) in self.view.cursors.items()}}}

    def _ids(self, name, result, document_id):
        """Evidence ID of each returned item, in item order; None if not registered."""
        if name == 'read':
            self.evidence.add_read(document_id, result)
            by_key = {(e.page_id, e.block_id, e.start, e.end, e.content): e.id
                      for e in self.evidence.all() if e.document_id == document_id}
            ids = []
            for item in result.get('items', []):
                try:
                    key = (UUID(item['page']['page_id']), UUID(item['block_id']),
                           item['range']['start'], item['range']['end'], item['content'])
                except (KeyError, TypeError, ValueError):
                    key = None
                ids.append(by_key.get(key))
            return ids
        if name == 'view_image':
            self.evidence.add_visual(result)
            by_key = {(e.asset_id, e.content): e.id for e in self.evidence.all() if e.origin == 'live_visual'}
            return [by_key.get((item.get('asset_id'), item.get('content'))) for item in result.get('items', [])]
        return []

    def _limit_error(self, name, tick):
        if len(self.trace) > self.max_tool_calls or tick - self.started > self.research_seconds:
            return {'ok': False, 'error': {'code': 'research_budget_exhausted'},
                    'hint': 'Finish with the verified evidence already returned; do not call another tool.'}
        if name == 'view_image' and self.visual_calls >= self.max_visual_calls:
            return {'ok': False, 'error': {'code': 'visual_budget_exhausted'}}
        return None

    def refuse(self, name: str, model_arguments, result: dict) -> None:
        """Record a call that is not a readable course tool call; it takes a slot."""
        self.trace.append({'event': 'tool', 'name': name, 'arguments': None,
                           'model_arguments': model_arguments, 'dispatched': False,
                           'ok': False, 'result': result, 'elapsed_seconds': 0.0})

    async def document_count(self) -> int:
        """How many course documents this run can read."""
        if self.view is None:
            self.view = ToolView(await self.tools.page_index())
        return len(self.view.documents)

    async def call(self, name: str, arguments: dict) -> tuple[str, bool]:
        """One model call with handles: translate, execute, render compact text."""
        if self.view is None:
            self.view = ToolView(await self.tools.page_index())
        try:
            raw = self.view.translate(name, arguments)
        except HandleError as exc:
            # A refused call still takes a tool slot, as a dispatched call does.
            tick = time.monotonic()
            row = {'event': 'tool', 'name': name, 'arguments': None, 'model_arguments': arguments,
                   'dispatched': False}
            self.trace.append(row)
            result = self._limit_error(name, tick) or {
                'ok': False, 'error': {'code': 'invalid_arguments', 'message': str(exc)}}
            row.update(ok=False, result=result, elapsed_seconds=0.0)
            raw = {}
        else:
            result = await self.execute(name, raw)
            row = self.trace[-1]
            row['model_arguments'] = arguments
        row['view'] = text = self.view.render(name, raw, result)
        return text, bool(result.get('ok'))

    async def execute(self, name: str, arguments: dict):
        """One call with backend arguments (UUIDs), counted in the budget."""
        tick = time.monotonic()
        row = {'event': 'tool', 'name': name, 'arguments': arguments, 'dispatched': False}
        self.trace.append(row)
        try:
            result = self._limit_error(name, tick)
            if result is None:
                row['dispatched'] = True
                result = await self.tools.execute(name, arguments)
                # Only an observation uses the visual budget; a failed call
                # still takes a tool slot.
                if name == 'view_image' and result.get('ok'):
                    self.visual_calls += 1
                if result.get('ok'):
                    ids = self._ids(name, result, UUID(arguments['document_id']) if name == 'read' else None)
                    if any(ids):
                        result = {**result, 'evidence_ids': list(dict.fromkeys(i for i in ids if i)),
                                  'item_evidence_ids': ids}
            row['ok'], row['result'] = result.get('ok', False), result
            return result
        except (ResearchQuotaExceeded, ResearchRunStopped):
            # The student's quota or the run ended: stop the run, not just this tool.
            row['ok'], row['error'] = False, 'run_stopped'
            raise
        except Exception as exc:
            row['ok'], row['error'] = False, type(exc).__name__
            result = {'ok': False, 'error': {'code': 'tool_failed', 'type': type(exc).__name__}}
            row['result'] = result
            return result
        finally:
            row['elapsed_seconds'] = time.monotonic() - tick
