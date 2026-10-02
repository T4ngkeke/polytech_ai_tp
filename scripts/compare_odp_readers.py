"""Compare our native ODP reader with Docling and MinerU on the same decks.

Not content acceptance. Three readers, one set of checks per deck:

- text: every line of the LibreOffice preview PDF (visible slides only, master
  page text removed) must be found in the reader's text, first anywhere in the
  deck, then on its own slide;
- hidden slides and speaker notes: their text must not appear;
- indentation: lines that start with spaces or tabs in the ODP must keep them;
- superscript and subscript runs: the reader must mark them somehow;
- tables: non-empty cells of native and embedded tables must appear in a table;
- raster images: the original bytes (or, for Docling, the pixel size) must come out;
- embedded objects: counted by type, so the gaps can be read next to the numbers.

The reference comes from LibreOffice and a small XML walk in this file, not
from backend/worker/odp_reader.py. Docling and MinerU run in their M0
environments through scripts/odp_m0; the renderer runs in the test compose.

  .venv/bin/python scripts/compare_odp_readers.py --work /tmp/<new dir> --output <result dir>
"""
import argparse
import json
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))

from backend.worker.odp_reader import read_odp  # noqa: E402
from backend.worker.slide_renderer import RenderSettings, pdf_page_texts, prepare_deck_task, render  # noqa: E402
from scripts.check_m2b_structured import compose_runner  # noqa: E402
from scripts.odp_m0 import docling_probe, mineru_probe  # noqa: E402

DOCLING_PYTHON = '/tmp/odp-m0-env/bin/python'
MINERU_PYTHON = '/tmp/odp-m0-mineru-env/bin/python'

NS = {
    'office': 'urn:oasis:names:tc:opendocument:xmlns:office:1.0',
    'style': 'urn:oasis:names:tc:opendocument:xmlns:style:1.0',
    'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
    'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
    'draw': 'urn:oasis:names:tc:opendocument:xmlns:drawing:1.0',
    'presentation': 'urn:oasis:names:tc:opendocument:xmlns:presentation:1.0',
    'xlink': 'http://www.w3.org/1999/xlink',
    'math': 'http://www.w3.org/1998/Math/MathML',
}
SUPERSCRIPT = str.maketrans('0123456789+-=()n', '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ')
SUBSCRIPT = str.maketrans('0123456789+-=()', '₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎')


def q(name):
    prefix, local = name.split(':')
    return f'{{{NS[prefix]}}}{local}'


def norm(value):
    value = unicodedata.normalize('NFKC', value or '').replace(' ', ' ')
    return re.sub(r'\s+', ' ', value).strip().lower()


BULLETS = '•◦▪▫■□►▶➢➤✓✗–—-*·'


def reference_line(value):
    """A PDF line without the bullet glyph LibreOffice draws in front of it."""
    value = norm(''.join(ch for ch in value if unicodedata.category(ch) != 'Co'))
    while value and (value[0] in BULLETS + ' ' or unicodedata.category(value[0]) == 'So'):
        value = value[1:]
    return value


# ---------------------------------------------------------------- reference
def raw_lines(paragraph):
    """Text of one text:p/text:h with text:s, tabs and line breaks kept."""
    out = []

    def walk(element):
        if element.tag == q('presentation:notes'):
            return
        if element.text:
            out.append(element.text)
        for child in element:
            if child.tag == q('text:s'):
                out.append(' ' * int(child.get(q('text:c'), '1')))
            elif child.tag == q('text:tab'):
                out.append('\t')
            elif child.tag == q('text:line-break'):
                out.append('\n')
            else:
                walk(child)
            if child.tail:
                out.append(child.tail)
    walk(paragraph)
    return ''.join(out).split('\n')


def paragraphs(element):
    for paragraph in element.iter():
        if paragraph.tag in (q('text:p'), q('text:h')):
            yield paragraph


def slide_paragraphs(slide, *, notes):
    for child in slide:
        if (child.tag == q('presentation:notes')) != notes:
            continue
        yield from paragraphs(child)


def text_styles(roots):
    """Style names whose text position is raised or lowered."""
    found = {}
    for root in roots:
        for style in root.iter(q('style:style')):
            props = style.find(q('style:text-properties'))
            position = props.get(q('style:text-position')) if props is not None else None
            if position and not position.startswith('0%') and position != '0':
                found[style.get(q('style:name'))] = 'sub' if position.startswith(('sub', '-')) else 'super'
    return found


def hidden_styles(root):
    hidden = set()
    for style in root.iter(q('style:style')):
        props = style.find(q('style:drawing-page-properties'))
        if props is not None and props.get(q('presentation:visibility')) == 'hidden':
            hidden.add(style.get(q('style:name')))
    return hidden


def cells_of(table):
    cells = []
    for cell in table.iter(q('table:table-cell')):
        value = norm(' '.join(' '.join(raw_lines(p)) for p in cell.iter(q('text:p'))))
        if value:
            cells.append(value)
    return cells


def reference(source, pdf_texts):
    with zipfile.ZipFile(source) as package:
        names = set(package.namelist())
        content = ET.fromstring(package.read('content.xml'))
        styles = ET.fromstring(package.read('styles.xml'))
        objects = {}
        for name in names:
            if name.endswith('/content.xml') and name.count('/') == 1:
                root = ET.fromstring(package.read(name))
                body = root.find(q('office:body'))
                if root.tag == q('math:math') or root.find(q('math:math')) is not None:
                    kind = 'formula'
                else:
                    kind = body[0].tag.split('}')[1] if body is not None and len(body) else 'unknown'
                objects[name.split('/')[0]] = (kind, root)
        package_bytes = {name: package.read(name) for name in names if name.startswith('Pictures/')}
    raised = text_styles([content, styles])
    hidden_names = hidden_styles(content)
    master_text = set()
    for master in styles.iter(q('style:master-page')):
        for paragraph in paragraphs(master):
            for line in raw_lines(paragraph):
                if norm(line):
                    master_text.add(norm(line))
    slides = content.find(q('office:body')).find(q('office:presentation')).findall(q('draw:page'))
    visible, hidden, notes = [], [], []
    ref = {'visible_slides': [], 'hidden_slides': [], 'indented': [], 'raised': [], 'tables': [],
           'images': [], 'objects': Counter(), 'object_text': [], 'private_use_chars': 0}
    for number, slide in enumerate(slides, start=1):
        is_hidden = slide.get(q('draw:style-name')) in hidden_names
        (ref['hidden_slides'] if is_hidden else ref['visible_slides']).append(number)
        lines = [line for p in slide_paragraphs(slide, notes=False) for line in raw_lines(p)]
        if not is_hidden:
            ref['private_use_chars'] += sum(1 for line in lines for ch in line if unicodedata.category(ch) == 'Co')
        (hidden if is_hidden else visible).extend(norm(line) for line in lines if norm(line))
        notes.extend(norm(line) for p in slide_paragraphs(slide, notes=True) for line in raw_lines(p) if norm(line))
        if is_hidden:
            continue
        for line in lines:
            stripped = line.lstrip(' \t')
            if stripped != line and len(stripped.strip()) >= 3:
                ref['indented'].append({'slide': number, 'line': line.rstrip()})
        for paragraph in slide_paragraphs(slide, notes=False):
            for element in paragraph.iter(q('text:span')):
                if element.get(q('text:style-name')) in raised:
                    value = ''.join(element.itertext())
                    if value.strip():
                        ref['raised'].append({'slide': number, 'kind': raised[element.get(q('text:style-name'))],
                                              'text': value})
        for table in slide.iter(q('table:table')):
            ref['tables'].append({'slide': number, 'kind': 'native', 'cells': cells_of(table)})
        for frame in slide.iter(q('draw:frame')):
            obj = frame.find(q('draw:object'))
            if obj is None:
                obj = frame.find(q('draw:object-ole'))
            if obj is not None:
                href = (obj.get(q('xlink:href')) or '').removeprefix('./').rstrip('/')
                kind, root = objects.get(href, ('ole' if obj.tag.endswith('ole') else 'missing', None))
                ref['objects'][kind] += 1
                if kind in ('drawing', 'graphics') and root is not None:
                    for paragraph in paragraphs(root):
                        for line in raw_lines(paragraph):
                            if len(norm(line)) >= 2:
                                ref['object_text'].append({'slide': number, 'line': norm(line)})
                if kind == 'spreadsheet' and root is not None:
                    cells = [c for t in root.iter(q('table:table')) for c in cells_of(t)]
                    ref['tables'].append({'slide': number, 'kind': 'embedded_spreadsheet', 'cells': cells})
                continue
            if frame.find(q('table:table')) is not None:
                continue  # the draw:image next to a native table is LibreOffice's own table preview
            for image in frame.findall(q('draw:image')):
                href = image.get(q('xlink:href')) or ''
                if href in package_bytes:
                    ref['images'].append({'slide': number, 'entry': href, 'bytes': package_bytes[href]})
        ref['objects']['shape_group'] += sum(1 for g in slide if g.tag == q('draw:g'))
    visible_set = set(visible)
    ref['hidden_only'] = sorted({line for line in hidden if len(line) >= 8 and line not in visible_set
                                 and line not in master_text})
    ref['notes_only'] = sorted({line for line in notes if len(line) >= 8 and line not in visible_set
                                and line not in master_text})
    ref['pdf_lines'] = []
    for index, page_text in enumerate(pdf_texts):
        slide = ref['visible_slides'][index] if index < len(ref['visible_slides']) else None
        for line in page_text.splitlines():
            value = reference_line(line)
            if len(value) < 3 or re.fullmatch(r'[\W\d_]+', value) or value in master_text:
                continue
            ref['pdf_lines'].append({'slide': slide, 'line': value})
    return ref


# ---------------------------------------------------------------- readers
def ours(source):
    started = time.monotonic()
    document = read_odp(source)
    seconds = time.monotonic() - started
    assets = {a.asset_id: a for a in document.assets}
    out = {'seconds': round(seconds, 3), 'slides': {}, 'raw_lines': [], 'tables': [], 'image_bytes': [],
           'marked_raised': 0, 'provenance': 'slide number, bbox and native object path on every block'}
    for page in document.pages:
        texts = []
        for block in page.blocks:
            texts.append(block.content or '')
            out['raw_lines'].extend((block.content or '').split('\n'))
            if block.kind == 'table':
                cells = [norm(c) for row in (block.content or '').split('\n') for c in row.split('\t') if norm(c)]
                out['tables'].append({'slide': page.source_slide_no, 'cells': cells})
            for asset_id in block.asset_ids:
                asset = assets.get(asset_id)
                if asset is not None and asset.data:
                    out['image_bytes'].append(asset.data)
        out['slides'][page.source_slide_no] = '\n'.join(texts)
    out['figure_blocks'] = sum(1 for p in document.pages for b in p.blocks if b.kind == 'figure')
    out['figure_blocks_empty'] = sum(1 for p in document.pages for b in p.blocks if b.kind == 'figure'
                                     and not (b.content or '').strip())
    return out


def docling(source, work):
    probe = docling_probe.probe_file(source, work, python=DOCLING_PYTHON)
    if probe.get('status') != 'ok':
        return {'status': probe.get('status'), 'probe': probe}
    raw = json.loads((work / 'docling_raw.json').read_text())
    items = {}
    for group_name in ('texts', 'tables', 'pictures', 'groups'):
        for item in raw.get(group_name, []) or []:
            items[item['self_ref']] = item
    order = []

    def walk(ref, slide):
        item = items.get(ref)
        if item is None:
            return
        name = item.get('name') or ''
        if item.get('label') == 'chapter' and re.fullmatch(r'slide-\d+', name):
            slide = int(name[6:]) + 1
        order.append((slide, item))
        for child in item.get('children', []):
            walk(child['$ref'], slide)
    for child in raw['body'].get('children', []):
        walk(child['$ref'], 0)
    out = {'seconds': probe['seconds'], 'slides': {}, 'raw_lines': [], 'tables': [], 'image_dims': [],
           'marked_raised': 0, 'provenance': 'none (prov is empty); slide only from the slide-N chapter groups'}
    for slide, item in order:
        ref = item['self_ref']
        if ref.startswith('#/texts/'):
            value = item.get('text', '')
            out['slides'][slide] = out['slides'].get(slide, '') + '\n' + value
            out['raw_lines'].extend(value.split('\n'))
            if (item.get('formatting') or {}).get('script') in ('sub', 'super'):
                out['marked_raised'] += 1
        elif ref.startswith('#/tables/'):
            cells = [norm(c.get('text', '')) for c in item['data'].get('table_cells', []) if norm(c.get('text', ''))]
            out['tables'].append({'slide': slide, 'cells': cells})
            out['slides'][slide] = out['slides'].get(slide, '') + '\n' + '\n'.join(cells)
    for asset in probe['assets']:
        if asset.get('width'):
            out['image_dims'].append((asset['width'], asset['height']))
    out['pictures'] = len(raw.get('pictures', []) or [])
    out['pictures_without_image'] = sum(1 for a in probe['assets'] if a.get('status') == 'no_image_data')
    return out


def mineru(source, work):
    probe = mineru_probe.probe_file(source, work, python=MINERU_PYTHON)
    if probe.get('status') != 'ok':
        return {'status': probe.get('status'), 'probe': probe}
    raw = json.loads((work / 'mineru_raw.json').read_text())
    out = {'seconds': probe['seconds'], 'slides': {}, 'raw_lines': [], 'tables': [], 'image_dims': [],
           'marked_raised': 0, 'provenance': 'page index only; no coordinates, no object id'}

    def runs(node):
        if isinstance(node, dict):
            if any('sup' in s or 'sub' in s for s in node.get('styles', []) or []):
                out['marked_raised'] += 1
            runs(node.get('content'))
        elif isinstance(node, list):
            for part in node:
                runs(part)
    for index, page in enumerate(raw['pages'], start=1):
        texts = []
        for item in page:
            runs(item.get('content'))
            if item.get('type') == 'table':
                blob = json.dumps(item.get('content'), ensure_ascii=False)
                cells = [norm(c) for c in re.split(r'<[^>]+>|\\n|\\t|"', blob) if norm(c) and norm(c) not in
                         ('type', 'content', 'text', ',', ':', '[', ']', '{', '}')]
                out['tables'].append({'slide': index, 'cells': cells})
                texts.append('\n'.join(cells))
                continue
            value = mineru_probe.item_text(item)
            texts.append(value)
            out['raw_lines'].extend(value.split('\n'))
        out['slides'][index] = '\n'.join(texts)
    shas = Counter(asset['sha256'] for asset in probe['assets'])
    out['placeholder_images'] = 0
    for asset in probe['assets']:
        if asset.get('width') and shas[asset['sha256']] < 3:
            out['image_dims'].append((asset['width'], asset['height']))
        else:
            out['placeholder_images'] += 1
    out['item_types'] = dict(probe['counts'])
    return out


# ---------------------------------------------------------------- scoring
def score(ref, got):
    import hashlib
    if 'slides' not in got:
        return {'status': got.get('status', 'failed')}
    joined = '\n'.join(got['slides'].values())
    private_use_out = sum(1 for ch in joined if unicodedata.category(ch) == 'Co')
    deck = norm(''.join(ch for ch in joined if unicodedata.category(ch) != 'Co'))
    deck_squashed = re.sub(r'\s', '', deck)
    by_slide = {k: norm(v) for k, v in got['slides'].items()}
    lines = ref['pdf_lines']
    anywhere = [x for x in lines if x['line'] in deck]
    loose = [x for x in lines if x['line'] in deck or re.sub(r'\s', '', x['line']) in deck_squashed]
    same_slide = [x for x in lines if x['line'] in by_slide.get(x['slide'], '')]
    missed = [x for x in lines if x not in loose]
    raw = set(line.rstrip() for line in got['raw_lines'])
    indent_exact = sum(1 for x in ref['indented'] if x['line'] in raw)
    indent_any = sum(1 for x in ref['indented']
                     if any(r.endswith(x['line'].strip()) and r[:1] in (' ', '\t') for r in raw))
    ref_cells = [(t['slide'], c) for t in ref['tables'] for c in t['cells']]
    table_text = {}
    for t in got['tables']:
        table_text.setdefault(t['slide'], set()).update(t['cells'])
    all_table_cells = set().union(*table_text.values()) if table_text else set()
    cells_in_table = sum(1 for _, c in ref_cells if c in all_table_cells)
    cells_as_text = sum(1 for _, c in ref_cells if c in deck)
    ref_sha = {hashlib.sha256(i['bytes']).hexdigest() for i in ref['images']}
    if 'image_bytes' in got:
        images = len(ref_sha & {hashlib.sha256(b).hexdigest() for b in got['image_bytes']})
    else:
        import pymupdf
        dims = Counter(got['image_dims'])
        images = 0
        for i in {hashlib.sha256(x['bytes']).hexdigest(): x for x in ref['images']}.values():
            try:
                pix = pymupdf.Pixmap(i['bytes'])
            except Exception:  # SVG/WMF originals: no pixel size to compare
                continue
            if dims[(pix.width, pix.height)]:
                dims[(pix.width, pix.height)] -= 1
                images += 1
    raised_marked = got.get('marked_raised', 0)
    if 'image_bytes' in got:  # ours: look for any mark in the text itself
        text = '\n'.join(got['slides'].values())
        raised_marked = sum(1 for r in ref['raised'] if any(
            # '^x' and '_x' are left out: code identifiers such as t_MINUS match them by chance
            form in text for form in ('<sup>' + r['text'], '<sub>' + r['text'],
                                      r['text'].translate(SUPERSCRIPT if r['kind'] == 'super' else SUBSCRIPT))
            if form != r['text']))
    return {
        'seconds': got['seconds'],
        'pdf_lines': len(lines),
        'pdf_lines_found': len(anywhere),
        'pdf_lines_found_ignoring_spaces': len(loose),
        'pdf_lines_found_on_same_slide': len(same_slide),
        'missed_examples': [f"s{x['slide']}: {x['line'][:90]}" for x in missed[:12]],
        'missed_total': len(missed),
        'hidden_only_lines': len(ref['hidden_only']),
        'hidden_only_leaked': sum(1 for x in ref['hidden_only'] if x in deck),
        'notes_only_lines': len(ref['notes_only']),
        'notes_only_leaked': sum(1 for x in ref['notes_only'] if x in deck),
        'indented_lines': len(ref['indented']),
        'indented_kept_exact': indent_exact,
        'indented_kept_some_indent': indent_any,
        'raised_runs': len(ref['raised']),
        'raised_marked': raised_marked,
        'table_cells': len(ref_cells),
        'table_cells_in_a_table': cells_in_table,
        'table_cells_as_text_anywhere': cells_as_text,
        'raster_images': len(ref_sha),
        'raster_images_out': images,
        'object_text_lines': len(ref['object_text']),
        'object_text_found': sum(1 for x in ref['object_text'] if x['line'] in deck),
        'private_use_chars_in_source': ref['private_use_chars'],
        'private_use_chars_out': private_use_out,
        'provenance': got['provenance'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True, help='new scratch directory for raw outputs')
    parser.add_argument('--output', type=Path, required=True, help='new directory for the result files')
    parser.add_argument('--corpus', type=Path, default=REPOSITORY / 'cours/odp_files')
    parser.add_argument('--only', help='one deck file name, for a trial run')
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=False)
    args.output.mkdir(parents=True, exist_ok=False)
    rows = []
    for source in sorted(args.corpus.glob(args.only or '*.odp')):
        work = args.work / source.stem
        (work / 'render').mkdir(parents=True)
        document = read_odp(source)
        task = prepare_deck_task(document, source, work / 'render', task_id='deck',
                                 settings=RenderSettings(want_page_images=False, timeout_seconds=600))
        outcome = render(task, runner=compose_runner(work))
        if outcome.status != 'ok':
            raise SystemExit(f'{source.name}: render {outcome.status} {outcome.reason}')
        ref = reference(source, pdf_page_texts(outcome.pdf.path))
        row = {'file': source.name, 'visible_slides': len(ref['visible_slides']),
               'hidden_slides': len(ref['hidden_slides']), 'embedded_objects': dict(ref['objects']),
               'soffice_version': outcome.soffice_version}
        for name, runner in (('ours', lambda: ours(source)), ('docling', lambda: docling(source, work / 'docling')),
                             ('mineru', lambda: mineru(source, work / 'mineru'))):
            got = runner()
            row[name] = score(ref, got)
            extra = {k: got[k] for k in ('figure_blocks', 'figure_blocks_empty', 'pictures', 'pictures_without_image',
                                         'item_types', 'placeholder_images') if k in got}
            row[name].update(extra)
        rows.append(row)
        (args.output / 'per_deck.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
        print(source.name, {n: (row[n].get('pdf_lines_found'), row[n].get('pdf_lines')) for n in
                            ('ours', 'docling', 'mineru')}, flush=True)
    totals = {}
    for name in ('ours', 'docling', 'mineru'):
        total = Counter()
        for row in rows:
            for key, value in row[name].items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    total[key] += value
        totals[name] = {k: round(v, 3) for k, v in total.items()}
    objects = Counter()
    for row in rows:
        objects.update(row['embedded_objects'])
    summary = {'decks': len(rows), 'visible_slides': sum(r['visible_slides'] for r in rows),
               'hidden_slides': sum(r['hidden_slides'] for r in rows), 'embedded_objects': dict(objects),
               'totals': totals}
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
