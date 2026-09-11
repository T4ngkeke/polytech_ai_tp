"""Sweep every page of the course PDFs through a vision model, without indexing.

The three-page probe compared against hand-written expectations. That does not
scale to 142 pages, so the ground truth here is the PDF text layer: the sweep
reports which source words the model dropped, which words it added, and which
characters it introduced that the page never contained. These are diagnostics,
not an acceptance pass. Output is never added to RAG.

Run with .venv/bin/python scripts/check_worker_vision_pages.py. Override
VISION_TEST_BASE_URL, VISION_TEST_MODEL, VISION_TEST_API_KEY, VISION_TEST_OUT.
"""
import base64
import json
import os
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The source listings use a visible-space marker; a plain space means the same
# thing. Typographic quotes and the extraction's spacing are layout, not content.
_EQUIVALENT = {'␣': ' ', '’': "'", '‘': "'", '“': '"',
               '”': '"', ' ': ' ', '−': '-', '–': '-',
               '—': '-', '•': ' '}
# The text layer flattens base markers and exponents: 111011 with a subscript 2
# extracts as "1110112". Writing the subscript is more faithful, not a change.
_EQUIVALENT.update({c: str(unicodedata.digit(c))
                    for c in '₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹'})
_LATEX = re.compile(r'\\[a-zA-Z]+|[${}^_]')
_WORD = re.compile(r'[0-9A-Za-zÀ-ſ]{2,}')


def normalise(text):
    """Fold away differences that are rendering, not transcription."""
    for source, target in _EQUIVALENT.items():
        text = text.replace(source, target)
    return unicodedata.normalize('NFC', text)


def words(text):
    return _WORD.findall(normalise(text).lower())


def _allowed_chars(source):
    # Anything already on the page, plus ASCII and accented Latin letters the
    # model may legitimately render differently. Everything else is suspect.
    allowed = set(normalise(source))
    allowed |= {chr(c) for c in range(32, 127)}
    allowed |= {c for c in map(chr, range(0xc0, 0x180)) if c.isalpha()}
    return allowed | {'\n', '\t'}


def dropped_lines(source, blocks, title=''):
    """Source lines whose words are wholly absent from the transcription.

    Word coverage cannot separate a real omission from a notation difference:
    a page written with LaTeX subscripts loses tokens without losing content.
    A line is reported when most of its words are gone, not merely some: a
    notation difference costs a line one or two tokens, an omission costs it
    all of them. Lines of fewer than two comparable words (page numbers, stray
    glyphs) are ignored, because one token cannot separate the two cases.
    """
    seen = set(words(title))
    for block in blocks:
        seen.update(words('\n'.join(block.get('lines', []))))
    dropped = []
    for line in source.split('\n'):
        tokens = words(line)
        if len(tokens) < 2:
            continue
        kept = sum(1 for t in tokens if t in seen)
        if kept / len(tokens) < 0.5:
            dropped.append(line.strip())
    return dropped


def compare_page(source, blocks, title=''):
    """Compare one model transcription against the PDF text layer of that page.

    Missing and invented words are whole-page bag comparisons: a block that
    merges bullets is not penalised. Foreign symbols are checked on code and
    text only, because formula blocks are LaTeX by instruction.
    """
    source_words = set(words(source))
    # A title slide carries most of its words in the title field.
    seen, symbol_text = list(words(title)), [title]
    for block in blocks:
        joined = '\n'.join(block.get('lines', []))
        if block.get('kind') == 'formula':
            joined = _LATEX.sub(' ', joined)
        else:
            symbol_text.append(joined)
        seen.extend(words(joined))
    seen_words = set(seen)
    missing = sorted(source_words - seen_words)
    invented = sorted(seen_words - source_words)
    allowed = _allowed_chars(source)
    foreign = sorted({c for c in normalise('\n'.join(symbol_text)) if c not in allowed})
    coverage = 1.0 if not source_words else round(
        len(source_words & seen_words) / len(source_words), 4)
    return {'coverage': coverage, 'missing': missing, 'invented': invented,
            'foreign_symbols': foreign,
            'dropped_lines': dropped_lines(source, blocks, title)}


def read_page(client, base, model, root, filename, page, schema, prompt):
    """One page, one request. Structural checks first, then the text-layer diff."""
    with __import__('pymupdf').open(root / 'cours' / filename) as doc:
        p = doc[page - 1]
        scale = 1600 / p.rect.width
        png = p.get_pixmap(matrix=__import__('pymupdf').Matrix(scale, scale)).tobytes('png')
        source = p.get_text()
    payload = {'model': model, 'temperature': 0, 'max_tokens': 4096,
               'response_format': schema, 'messages': [{'role': 'user', 'content': [
                   {'type': 'text', 'text': prompt},
                   {'type': 'image_url', 'image_url': {
                       'url': 'data:image/png;base64,' + base64.b64encode(png).decode()}}]}]}
    record = {'file': filename, 'page': page, 'source_chars': len(source.strip())}
    start = time.monotonic()
    try:
        resp = client.post(base + '/chat/completions', json=payload)
        record['http_status'] = resp.status_code
        body = resp.json()
    except Exception as exc:                     # noqa: BLE001 - diagnostics only
        record.update(error=type(exc).__name__ + ': ' + str(exc)[:200],
                      seconds=round(time.monotonic() - start, 2))
        return record
    record['seconds'] = round(time.monotonic() - start, 2)
    try:
        choice = body['choices'][0]
        record['finish_reason'] = choice['finish_reason']
        if choice['finish_reason'] != 'stop':
            raise ValueError('truncated or unfinished')
        from backend.worker.context import _final_part
        text = _final_part(choice['message']['content'] or '')
        if text.startswith('```'):
            text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
        data = json.loads(text)
        if set(data) != {'title', 'blocks', 'unreadable'}:
            raise ValueError('invalid page schema')
        for b in data['blocks']:
            if set(b) != {'kind', 'lines'} or not isinstance(b['lines'], list):
                raise ValueError('invalid block schema')
            if any(re.search(r'</?(think|analysis|reasoning)', l, re.I) for l in b['lines']):
                raise ValueError('reasoning inside block')
        record['structural_ok'] = True
        record['unreadable'] = data['unreadable']
        record['parsed'] = data
        record['diff'] = compare_page(source, data['blocks'], data['title'])
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        record.update(structural_ok=False, reason=str(exc)[:200])
    return record


def main():
    import argparse
    import httpx
    from scripts.check_worker_vision import PROMPT, RESPONSE_SCHEMA
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--files', nargs='*', default=['main-2.pdf', 'main-3.pdf'])
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--limit', type=int, default=0, help='first N pages per file')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = Path(os.getenv('VISION_TEST_OUT', root / 'documentation/worker-vision-2026-09-08/full-sweep'))
    out.mkdir(parents=True, exist_ok=True)
    base = os.getenv('VISION_TEST_BASE_URL', 'http://127.0.0.1:8001/v1').rstrip('/')
    model = os.getenv('VISION_TEST_MODEL', 'ornith')
    prompt = PROMPT.replace(
        '{"kind":"code|text|formula|table|navigation","text":"..."}',
        '{"kind":"code|text|formula|table|navigation","lines":["one physical line"]}'
    ) + '\nEach lines item is one real line. Return only fields required by the schema.'
    import pymupdf
    jobs = []
    for filename in args.files:
        with pymupdf.open(root / 'cours' / filename) as doc:
            pages = range(1, (args.limit or doc.page_count) + 1)
        jobs.extend((filename, p) for p in pages)
    started = time.monotonic()
    with httpx.Client(timeout=600, headers={'Authorization': 'Bearer ' + os.getenv('VISION_TEST_API_KEY', 'local')}) as client:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(read_page, client, base, model, root, f, p, RESPONSE_SCHEMA, prompt)
                       for f, p in jobs]
            records = []
            for i, fut in enumerate(futures, 1):
                records.append(fut.result())
                if i % 10 == 0:
                    print('done', i, '/', len(futures), flush=True)
    records.sort(key=lambda r: (r['file'], r['page']))
    report = {'model': model, 'workers': args.workers,
              'wall_seconds': round(time.monotonic() - started, 1), 'pages': records}
    (out / 'pages.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    ok = [r for r in records if r.get('structural_ok')]
    print('pages:', len(records), '| structural ok:', len(ok),
          '| wall:', report['wall_seconds'], 's')
    print('Saved:', out / 'pages.json')


if __name__ == '__main__':
    main()
