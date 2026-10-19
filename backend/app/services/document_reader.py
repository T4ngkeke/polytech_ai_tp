"""M2-C: deterministic reading of one structured revision.

open / navigate / read / grep, as the roadmap's tool contract describes them,
without any agent. The caller first resolves the revision through
document_access.readable_revision, which applies every permission rule (a
student never gets a draft, an archived or deleted document is not readable).
Everything here then stays inside that one revision.

Every result has the common envelope: ok, items, issues, truncated,
next_cursor, usage.budget_units, error. budget_units counts characters of
returned text; it is not a billed token count.

What is read is the effective content: the native text, or the teacher's text
where a teacher changed a block. grep never matches descriptions, so a word a
model wrote about a picture cannot look like a match in the course text.
"""
import base64
import hashlib
import hmac
import json
import re

from sqlalchemy import select

from backend.app.config import settings
from backend.app.models import DocumentAsset, DocumentBlock, DocumentBlockAsset, DocumentPage, DocumentReview

OPEN_BATCH = 20
GREP_BATCH = 20
NAVIGATE_MAX = 3
DEFAULT_BUDGET = 8000
MAX_BUDGET = 20000
SUMMARY_CHARS = 160
CURSOR_VERSION = 1


class ReaderError(ValueError):
    """A tool error. code is one of the roadmap error codes."""

    def __init__(self, code: str, message: str = ''):
        super().__init__(message or code)
        self.code = code


def envelope(items, *, issues=(), truncated=False, next_cursor=None, budget_units=0, **extra):
    return {'ok': True, 'items': items, 'issues': list(issues), 'truncated': truncated,
            'next_cursor': next_cursor, 'usage': {'budget_units': budget_units}, 'error': None, **extra}


def error_envelope(exc: ReaderError):
    return {'ok': False, 'items': [], 'issues': [], 'truncated': False, 'next_cursor': None,
            'usage': {'budget_units': 0}, 'error': {'code': exc.code, 'message': str(exc),
                                                   'retryable': exc.code in ('budget_exhausted',)}}


# ---------------------------------------------------------------------------
# Cursors: opaque, signed, bound to one reader, one revision and one operation
# ---------------------------------------------------------------------------

def _mac(body: bytes) -> str:
    key = hashlib.sha256(('document-reader-cursor:' + settings.JWT_SECRET).encode()).digest()
    return hmac.new(key, body, hashlib.sha256).hexdigest()[:32]


def encode_cursor(*, reader: str, revision_id, op: str, args: dict, position: dict) -> str:
    body = json.dumps({'v': CURSOR_VERSION, 'r': reader, 'rev': str(revision_id), 'op': op,
                       'a': args, 'p': position}, sort_keys=True, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(body).decode().rstrip('=') + '.' + _mac(body)


def decode_cursor(cursor: str, *, reader: str, revision_id, op: str, args: dict) -> dict:
    try:
        raw, mac = cursor.rsplit('.', 1)
        body = base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4))
        if not hmac.compare_digest(mac, _mac(body)):
            raise ValueError
        data = json.loads(body)
    except (ValueError, TypeError):
        raise ReaderError('invalid_cursor') from None
    # A cursor from another reader, revision, tool or query is refused.
    if (data.get('v') != CURSOR_VERSION or data.get('r') != reader or data.get('rev') != str(revision_id)
            or data.get('op') != op or data.get('a') != args):
        raise ReaderError('invalid_cursor')
    return data['p']


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

async def _pages(db, rev):
    return list((await db.scalars(select(DocumentPage).where(DocumentPage.revision_id == rev.id)
                                  .order_by(DocumentPage.order_index))).all())


async def _blocks(db, rev, page_id=None):
    query = select(DocumentBlock).where(DocumentBlock.revision_id == rev.id)
    if page_id is not None:
        query = query.where(DocumentBlock.page_id == page_id)
    return list((await db.scalars(query)).all())


async def reading_order(db, rev):
    """Pages in order, and every block in page then block order."""
    pages = await _pages(db, rev)
    rank = {p.page_id: p.order_index for p in pages}
    blocks = sorted(await _blocks(db, rev), key=lambda b: (rank.get(b.page_id, 1 << 30), b.order_index))
    return pages, blocks


def _summary(text: str | None) -> str:
    text = re.sub(r'\s+', ' ', text or '').strip()
    return text if len(text) <= SUMMARY_CHARS else text[:SUMMARY_CHARS - 1] + '…'


def _page_ref(page):
    return {'page_id': str(page.page_id), 'order_index': page.order_index,
            'source_slide_no': page.source_slide_no, 'preview_page_no': page.preview_page_no,
            'preview_asset_id': str(page.preview_asset_id) if page.preview_asset_id else None}


def block_view(block, page, *, teacher: bool, assets=()):
    """One block as a reader sees it. A student gets no native backup text."""
    ref = block.source_ref or {}
    view = {
        'block_id': str(block.block_id), 'page': _page_ref(page), 'order_index': block.order_index,
        'type': block.kind, 'content': block.content, 'content_format': block.content_format,
        'content_description': block.content_description, 'context_description': block.context_description,
        'description_origin': block.description_origin, 'section_path': list(block.section_path or []),
        'source_completeness': block.source_completeness, 'index_state': block.index_state,
        'edited_by_teacher': block.edited_by_teacher, 'issues': list(block.issues or []),
        'source': {'format': ref.get('format'), 'source_slide_no': ref.get('source_slide_no'),
                   'native_object_ids': ref.get('native_object_ids', []),
                   'extraction_method': ref.get('extraction_method'), 'bbox': ref.get('bbox'),
                   'content_origin': ref.get('content_origin', 'native')},
        'assets': list(assets),
    }
    if teacher:
        view['source_text'] = block.source_text
        view['structure'] = block.structure
        view['region'] = page_region(ref)
    return view


def page_region(ref: dict) -> list[float] | None:
    """Where MinerU found the block on its page, [x0, y0, x1, y1] in thousandths
    of the page width and height (the page preview has the same shape), so a
    teacher sees which picture a block is. None when MinerU gave no valid box."""
    box = ((ref.get('legacy_ref') or {}).get('mineru_bbox_per_mille'))
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    return [x0, y0, x1, y1] if 0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000 else None


async def _block_assets(db, rev, block_ids, *, teacher: bool):
    if not block_ids:
        return {}
    rows = (await db.execute(select(DocumentBlockAsset, DocumentAsset)
        .join(DocumentAsset, DocumentAsset.id == DocumentBlockAsset.asset_id)
        .where(DocumentBlockAsset.revision_id == rev.id, DocumentBlockAsset.block_id.in_(block_ids)))).all()
    out = {}
    for link, asset in rows:
        # A student only sees assets they may open.
        if not teacher and asset.access_class != 'student_visible':
            continue
        out.setdefault(link.block_id, []).append({'asset_id': str(asset.id), 'kind': asset.kind,
                                                  'mime': asset.mime, 'role': link.role,
                                                  'width': asset.width, 'height': asset.height})
    return out


def _unavailable(page, blocks):
    """What this page is known to miss. Said, never hidden."""
    out = [{'scope': 'page', 'code': i.get('code'), 'message': i.get('message')}
           for i in (page.issues or []) if i.get('resolution', 'pending') == 'pending']
    for block in blocks:
        if block.source_completeness != 'complete':
            out.append({'scope': 'block', 'block_id': str(block.block_id), 'type': block.kind,
                        'code': 'source_' + block.source_completeness,
                        'message': 'this block was not read completely from the source; see the page preview'})
    return out


async def page_view(db, rev, page_id, *, teacher: bool):
    """The page endpoint: metadata, ordered blocks, issues, mapping, assets."""
    pages = await _pages(db, rev)
    page = next((p for p in pages if p.page_id == page_id), None)
    if page is None:
        raise ReaderError('not_found')
    blocks = sorted(await _blocks(db, rev, page.page_id), key=lambda b: b.order_index)
    assets = await _block_assets(db, rev, [b.block_id for b in blocks], teacher=teacher)
    position = pages.index(page)
    view = {
        'document_id': str(rev.document_id), 'revision_id': str(rev.id),
        'publication_state': rev.publication_state, 'processing_state': rev.processing_state,
        'page': {**_page_ref(page), 'title': page.title, 'state': page.state, 'issues': list(page.issues or []),
                 'width_cm': page.width, 'height_cm': page.height,
                 'preview_asset_id': str(page.preview_asset_id) if page.preview_asset_id else None,
                 # Unproven mapping: no page number to quote, and the reason stays visible.
                 'preview_mapping': 'verified' if page.preview_page_no is not None else 'unverified'},
        'blocks': [block_view(b, page, teacher=teacher, assets=assets.get(b.block_id, ())) for b in blocks],
        'unavailable': _unavailable(page, blocks),
        'preview_pdf_asset_id': str(rev.preview_pdf_asset_id) if rev.preview_pdf_asset_id else None,
        'navigation': {'previous_page_id': str(pages[position - 1].page_id) if position > 0 else None,
                       'next_page_id': str(pages[position + 1].page_id) if position + 1 < len(pages) else None,
                       'page_count': len(pages)},
        'teacher_revised': any(b.edited_by_teacher for b in blocks),
    }
    if teacher:
        reviews = (await db.scalars(select(DocumentReview).where(
            DocumentReview.revision_id == rev.id, DocumentReview.status == 'pending',
            DocumentReview.page_no == page.source_slide_no))).all()
        view['reviews'] = [review_view(r) for r in reviews]
    return view


def review_view(row):
    ref = row.source_ref or {}
    return {'id': str(row.id), 'kind': row.kind, 'block_id': str(row.block_id) if row.block_id else None,
            'source_slide_no': row.page_no, 'reason': row.reason, 'candidate_content': row.candidate_content,
            'status': row.status, 'revision': row.revision, 'final_content': row.final_content,
            'model': ref.get('model'), 'prompt_version': ref.get('prompt_version'),
            'latency_ms': ref.get('latency_ms'), 'usage': ref.get('usage'), 'usage_status': ref.get('usage_status'),
            'uncertainties': ref.get('uncertainties', []), 'relations_verified': ref.get('relations_verified'),
            'why': ref.get('why')}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

async def open_document(db, rev, *, reader: str, page_id=None, block_id=None, cursor=None):
    """A table of contents, 20 pages at a time, from an anchor or a cursor."""
    pages, blocks = await reading_order(db, rev)
    args = {}
    if cursor:
        start = int(decode_cursor(cursor, reader=reader, revision_id=rev.id, op='open', args=args)['start'])
    elif block_id is not None:
        block = next((b for b in blocks if b.block_id == block_id), None)
        if block is None:
            raise ReaderError('not_found')
        start = next(p.order_index for p in pages if p.page_id == block.page_id)
    elif page_id is not None:
        page = next((p for p in pages if p.page_id == page_id), None)
        if page is None:
            raise ReaderError('not_found')
        start = page.order_index
    else:
        start = 0
    by_page = {}
    for block in blocks:
        by_page.setdefault(block.page_id, []).append(block)
    items, units = [], 0
    for page in pages[start:start + OPEN_BATCH]:
        page_blocks = by_page.get(page.page_id, [])
        first = next((b for b in page_blocks if b.kind not in ('navigation',) and (b.content or '').strip()), None)
        summary = _summary(first.content if first else '')
        units += len(summary)
        items.append({**_page_ref(page), 'title': page.title, 'state': page.state,
                      'block_count': len(page_blocks), 'summary': summary,
                      'first_block_id': str(page_blocks[0].block_id) if page_blocks else None})
    end = start + len(items)
    more = end < len(pages)
    return envelope(items, truncated=more, budget_units=units,
                    next_cursor=encode_cursor(reader=reader, revision_id=rev.id, op='open', args=args,
                                              position={'start': end}) if more else None,
                    page_count=len(pages), revision_id=str(rev.id), document_id=str(rev.document_id))


async def navigate(db, rev, *, reader: str, anchor_block_id, direction: str, unit: str = 'block', count: int = 1):
    """Move in true reading order. Never sorted by relevance."""
    if direction not in ('next', 'previous') or unit not in ('block', 'page') or not 1 <= count <= NAVIGATE_MAX:
        raise ReaderError('invalid_arguments')
    pages, blocks = await reading_order(db, rev)
    index = next((i for i, b in enumerate(blocks) if b.block_id == anchor_block_id), None)
    if index is None:
        raise ReaderError('not_found')
    page_by_id = {p.page_id: p for p in pages}
    items = []
    if unit == 'block':
        step = 1 if direction == 'next' else -1
        position = index + step
        while 0 <= position < len(blocks) and len(items) < count:
            block = blocks[position]
            items.append({'block_id': str(block.block_id), 'type': block.kind,
                          'page': _page_ref(page_by_id[block.page_id]), 'summary': _summary(block.content)})
            position += step
    else:
        page_index = page_by_id[blocks[index].page_id].order_index
        wanted = range(page_index + 1, page_index + 1 + count) if direction == 'next' \
            else range(page_index - 1, page_index - 1 - count, -1)
        for number in wanted:
            if 0 <= number < len(pages):
                page = pages[number]
                first = next((b for b in blocks if b.page_id == page.page_id), None)
                items.append({**_page_ref(page), 'title': page.title,
                              'first_block_id': str(first.block_id) if first else None,
                              'summary': _summary(first.content if first else '')})
    return envelope(items, budget_units=sum(len(i['summary']) for i in items),
                    at_start=direction == 'previous' and len(items) < count,
                    at_end=direction == 'next' and len(items) < count)


async def read(db, rev, *, reader: str, teacher: bool, page_id=None, block_id=None, start=None, end=None,
               cursor=None, budget: int = DEFAULT_BUDGET):
    """Read effective content. A whole block by default; a clear cut and a
    cursor when the budget is smaller than the block."""
    if (page_id is None) == (block_id is None) or not 1 <= budget <= MAX_BUDGET:
        raise ReaderError('invalid_arguments')
    pages, blocks = await reading_order(db, rev)
    page_by_id = {p.page_id: p for p in pages}
    if block_id is not None:
        block = next((b for b in blocks if b.block_id == block_id), None)
        if block is None:
            raise ReaderError('not_found')
        args = {'block_id': str(block_id)}
        length = len(block.content or '')
        if cursor:
            position = decode_cursor(cursor, reader=reader, revision_id=rev.id, op='read', args=args)
            start, end = position['start'], position['end']
        start = 0 if start is None else start
        end = length if end is None else end
        if not 0 <= start <= end <= length:
            raise ReaderError('invalid_arguments')
        stop = min(end, start + budget)
        view = block_view(block, page_by_id[block.page_id], teacher=teacher,
                          assets=(await _block_assets(db, rev, [block.block_id], teacher=teacher)).get(block.block_id, ()))
        view.update(content=(block.content or '')[start:stop], range={'start': start, 'end': stop},
                    block_length=length, returned_complete_block=(start == 0 and stop == length))
        more = stop < end
        return envelope([view], issues=view['issues'], truncated=more, budget_units=stop - start,
                        next_cursor=encode_cursor(reader=reader, revision_id=rev.id, op='read', args=args,
                                                  position={'start': stop, 'end': end}) if more else None)
    page = page_by_id.get(page_id)
    if page is None:
        raise ReaderError('not_found')
    args = {'page_id': str(page_id)}
    page_blocks = [b for b in blocks if b.page_id == page.page_id]
    first, offset = 0, 0
    if cursor:
        position = decode_cursor(cursor, reader=reader, revision_id=rev.id, op='read', args=args)
        first, offset = position['block'], position['offset']
    assets = await _block_assets(db, rev, [b.block_id for b in page_blocks], teacher=teacher)
    items, used, next_position = [], 0, None
    for number in range(first, len(page_blocks)):
        block = page_blocks[number]
        content = block.content or ''
        begin = offset if number == first else 0
        room = budget - used
        if room <= 0:
            next_position = {'block': number, 'offset': begin}
            break
        stop = min(len(content), begin + room)
        view = block_view(block, page, teacher=teacher, assets=assets.get(block.block_id, ()))
        view.update(content=content[begin:stop], range={'start': begin, 'end': stop}, block_length=len(content),
                    returned_complete_block=(begin == 0 and stop == len(content)))
        items.append(view)
        used += stop - begin
        if stop < len(content):
            next_position = {'block': number, 'offset': stop}
            break
    more = next_position is not None
    return envelope(items, issues=_unavailable(page, page_blocks), truncated=more, budget_units=used,
                    next_cursor=encode_cursor(reader=reader, revision_id=rev.id, op='read', args=args,
                                              position=next_position) if more else None,
                    page=_page_ref(page), unavailable=_unavailable(page, page_blocks))


async def grep(db, rev, *, reader: str, pattern: str, case_sensitive: bool = False, cursor=None):
    """Literal substring search over effective content. No regex, no stemming.

    Ranges are Unicode character offsets [start, end) in the block content,
    so they point at exactly the text that matched.
    """
    if not isinstance(pattern, str) or not 1 <= len(pattern) <= 200:
        raise ReaderError('invalid_arguments')
    args = {'pattern': pattern, 'case_sensitive': bool(case_sensitive)}
    start_block, start_offset = 0, 0
    if cursor:
        position = decode_cursor(cursor, reader=reader, revision_id=rev.id, op='grep', args=args)
        start_block, start_offset = position['block'], position['offset']
    pages, blocks = await reading_order(db, rev)
    page_by_id = {p.page_id: p for p in pages}
    # re.escape keeps the search literal; IGNORECASE keeps the offsets of the
    # original text, where lower()/casefold() could change its length.
    finder = re.compile(re.escape(pattern), 0 if case_sensitive else re.IGNORECASE)
    items, next_position = [], None
    for number in range(start_block, len(blocks)):
        block = blocks[number]
        content = block.content or ''
        for match in finder.finditer(content, start_offset if number == start_block else 0):
            if len(items) == GREP_BATCH:
                next_position = {'block': number, 'offset': match.start()}
                break
            left, right = max(0, match.start() - 60), min(len(content), match.end() + 60)
            items.append({'block_id': str(block.block_id), 'type': block.kind,
                          'page': _page_ref(page_by_id[block.page_id]),
                          'range': {'start': match.start(), 'end': match.end()},
                          'context': content[left:right], 'context_range': {'start': left, 'end': right},
                          'edited_by_teacher': block.edited_by_teacher})
        if next_position:
            break
    more = next_position is not None
    return envelope(items, truncated=more, budget_units=sum(len(i['context']) for i in items),
                    next_cursor=encode_cursor(reader=reader, revision_id=rev.id, op='grep', args=args,
                                              position=next_position) if more else None)
