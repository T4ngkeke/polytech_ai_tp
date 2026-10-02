"""Generate a review workbench. Ungraded outputs never receive inherited scores."""
import argparse
import html
import json
import statistics
from collections import Counter
from pathlib import Path

from .core import validate_review
from .suite import OUTPUT, ROOT


def problem_counts(row, review):
    """Problems = non-correct checks + added errors. A problem is flagged only if the page carries a
    review or error record AND the reviewer confirmed (flagged: true) that a record covers it; otherwise silent."""
    items = [c for c in review['checks'] if c['result'] != 'correct'] + list(review.get('added_errors', []))
    any_flag = bool(row['reviews'] or row['errors'])
    flagged = sum(1 for x in items if any_flag and x.get('flagged') is True)
    return len(items), flagged, len(items) - flagged


def generate(root):
    runs = {}
    pages = {}
    for folder in sorted((root / 'runs').glob('*')):
        rows = []
        for path in sorted(folder.glob('*.page.json')):
            row = json.loads(path.read_text())
            review_path = path.with_suffix('.review.json')
            review = None
            if review_path.exists():
                review = json.loads(review_path.read_text())
                validate_review(row, review)
            rows.append((row, review))
            pages.setdefault(path.name, []).append((folder.name, row, review))
        if not rows:
            continue
        times = [r['seconds'] for r, _ in rows]
        runs[folder.name] = {
            'pages': len(rows), 'status': dict(Counter(r['status'] for r, _ in rows)),
            'review_regions': sum(len(r['reviews']) for r, _ in rows),
            'errors': sum(len(r['errors']) for r, _ in rows),
            'total_seconds': sum(times), 'median_seconds': statistics.median(times),
            'max_seconds': max(times), 'calls': sum(r['calls'] for r, _ in rows),
            'output_chars': sum(e.get('output_chars',0) for r,_ in rows for e in r.get('metadata',{}).get('requests',[])),
            'truncated_requests': sum('length' in e.get('finish_reasons',[]) for r,_ in rows for e in r.get('metadata',{}).get('requests',[])),
            'token_usage': {k:sum(u.get(k,0) or 0 for r,_ in rows for u in r.get('usage',[])) for k in ('prompt_tokens','completion_tokens','total_tokens')},
            'graded_pages': sum(v is not None for _, v in rows),
            'checks': dict(Counter(c['result'] for _, v in rows if v for c in v['checks'])),
            'added_errors': sum(len(v.get('added_errors', [])) for _, v in rows if v),
            'problems': sum(problem_counts(r, v)[0] for r, v in rows if v),
            'flagged_problems': sum(problem_counts(r, v)[1] for r, v in rows if v),
            'silent_problems': sum(problem_counts(r, v)[2] for r, v in rows if v),
        }
    esc = html.escape
    body = ['<!doctype html><meta charset="utf-8"><title>Offline content review</title>',
            '<style>body{font-family:system-ui;margin:24px} .grid{display:flex;gap:16px;overflow:auto}'
            'article{min-width:400px;max-width:600px}pre{white-space:pre-wrap;overflow-wrap:anywhere}'
            'img{max-width:600px;max-height:700px}details{border-bottom:1px solid #aaa;padding:12px}</style>',
            '<h1>28-page development review</h1><p>Automatic completion does not establish content accuracy. Pages without a review are ungraded; review candidates are excluded from searchable content.</p>']
    for name, items in pages.items():
        stem = name.removesuffix('.page.json')
        body.append(f'<details><summary>{esc(stem)}</summary><div class="grid">')
        preview = root / 'previews' / f'{stem}.png'
        if preview.exists():
            body.append(f'<article><h2>Original page</h2><img loading="lazy" src="previews/{esc(stem)}.png"></article>')
        for variant, row, review in items:
            review_text = json.dumps(review, ensure_ascii=False, indent=2) if review else 'Ungraded'
            body.append(f'<article><h2>{esc(variant)}</h2><p>{esc(row["status"])}</p>'
                        f'<pre>{esc(row["searchable_text"])}</pre><h3>Pending review / runtime errors</h3>'
                        f'<pre>{esc(json.dumps(row["reviews"] + row["errors"],ensure_ascii=False,indent=2))}</pre>'
                        f'<h3>Content assessment</h3><pre>{esc(review_text)}</pre></article>')
        body.append('</div></details>')
    (root / 'comparison.html').write_text('\n'.join(body))
    (root / 'summary.json').write_text(json.dumps(runs, ensure_ascii=False, indent=2))
    return runs


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    print(json.dumps(generate(args.output), ensure_ascii=False, indent=2))
