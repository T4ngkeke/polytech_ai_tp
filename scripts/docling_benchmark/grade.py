"""Bind explicit reviewer judgments to exact output bytes; never auto-grade semantic facts."""
import argparse,json
from pathlib import Path
from .core import bind_review
from .suite import load_suite


def record(path,judgment):
    page=json.loads(path.read_text())
    stem=path.name.removesuffix('.page.json')
    # A run folder is <output>/runs/<variant-round>/; its output root keeps the suite it was run with.
    saved=path.parents[2]/'suite.json'
    suite=json.loads(saved.read_text()) if saved.exists() else load_suite()
    expected={c['check_id'] for c in suite['checks'][stem]}
    checks=judgment['checks']
    ids=[c['check_id'] for c in checks]
    if set(ids)!=expected or len(ids)!=len(expected):
        raise ValueError('Every fixed check must appear exactly once')
    for c in checks:
        if c['result'] not in ('correct','missing','wrong','mixed','unreviewed') or not c.get('evidence','').strip():
            raise ValueError('Each judgment needs a valid result and concrete evidence')
    review=bind_review(page,checks,reviewer=judgment['reviewer'],kind=judgment['reviewer_kind'],added_errors=judgment.get('added_errors',[]))
    target=path.with_suffix('.review.json')
    if target.exists():raise ValueError('Review exists; preserve it before a deliberate revision')
    target.write_text(json.dumps(review,ensure_ascii=False,indent=2))
    return review


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('page',type=Path);p.add_argument('judgment',type=Path)
    args=p.parse_args();record(args.page,json.loads(args.judgment.read_text()))
