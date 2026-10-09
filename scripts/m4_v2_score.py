"""Facet scores for a benchmark v2/v3/v4 run of scripts/m4_evaluate.py.

Usage: .venv/bin/python scripts/m4_v2_score.py OUTPUT_DIR

Scores are the ones defined in the benchmark README before any run:
facet coverage, complete questions, citation precision. Content is not scored
here; SIDE_BY_SIDE_V2.md lists every answer next to its facets for review.
"""
from __future__ import annotations

import collections
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / 'documentation/ingestion-agent-acceptance/m4-agent/2026-10-05-benchmark-v2/questions.json'


def nearest_rank(values, q):
    values = sorted(values)
    return values[max(0, -(-len(values) * q // 100) - 1)] if values else None


def score_trial(row, question, names):
    cited = {tuple(p) for p in row['cited_pages']}
    pages = lambda refs: {(names[r['doc']], r['page']) for r in refs}
    related = pages(question.get('related', []))
    allowed = set(related)
    facets = []
    for facet in question['facets']:
        acceptable = pages(facet['acceptable'])
        allowed |= acceptable
        if facet['kind'] == 'course':
            facets.append({'facet_id': facet['facet_id'], 'covered': bool(cited & acceptable),
                           'visual': bool(facet.get('visual'))})
    good = len(cited & allowed)
    return {'facets': facets, 'covered': sum(f['covered'] for f in facets), 'total': len(facets),
            # A question with only absent facets is scored on content, never "complete" here.
            'complete': bool(facets) and all(f['covered'] for f in facets),
            'cited': len(cited), 'cited_ok': good,
            'precision': good / len(cited) if cited else None,
            'partial': row['stop_reason'].startswith('partial_')}


def main(output: Path):
    manifest = output / 'manifest.json'
    benchmark = json.loads(manifest.read_text()).get('benchmark') if manifest.exists() else 'v2'
    questions_file = {'v3': QUESTIONS.parents[1] / '2026-10-05-benchmark-v3' / 'questions.json',
                      'v4': QUESTIONS.parents[1] / '2026-10-06-benchmark-v4' / 'questions.json'
                      }.get(benchmark, QUESTIONS)
    spec = json.loads(questions_file.read_text())
    names = {k: Path(d['pdf']).name for k, d in spec['documents'].items()}
    questions = {q['question_id']: q for q in spec['questions']}
    rows = json.loads((output / 'results.json').read_text())
    by_variant = collections.defaultdict(list)
    for row in rows:
        if row['question_id'] in questions:
            row['score'] = score_trial(row, questions[row['question_id']], names)
            by_variant[row['variant']].append(row)

    summary = {}
    for variant, trials in sorted(by_variant.items()):
        s = [t['score'] for t in trials]
        visual = [f for x in s for f in x['facets'] if f['visual']]
        usage = [u for t in trials for u in t['usage']]
        summary[variant] = {
            'trials': len(trials),
            'facet_coverage': f"{sum(x['covered'] for x in s)}/{sum(x['total'] for x in s)}",
            'facet_coverage_rate': round(sum(x['covered'] for x in s) / sum(x['total'] for x in s), 3),
            'complete_questions': sum(x['complete'] for x in s),
            'trials_with_course_facets': sum(x['total'] > 0 for x in s),
            'citation_precision': round(sum(x['cited_ok'] for x in s) / max(1, sum(x['cited'] for x in s)), 3),
            'cited_pages_per_trial': round(statistics.mean(x['cited'] for x in s), 2),
            'zero_citation_trials': sum(x['cited'] == 0 for x in s),
            'partial_fallbacks': sum(x['partial'] for x in s),
            'partial_fallback_facets_covered': sum(x['covered'] for x in s if x['partial']),
            'visual_facet_coverage': f"{sum(f['covered'] for f in visual)}/{len(visual)}",
            'stop_reasons': dict(collections.Counter(t['stop_reason'] for t in trials)),
            'median_seconds': round(statistics.median(t['elapsed_seconds'] for t in trials), 2),
            'p95_seconds': nearest_rank([t['elapsed_seconds'] for t in trials], 95),
            'tool_calls': sum(t['tool_calls'] for t in trials),
            'model_input_tokens': sum(u.get('input_tokens') or 0 for u in usage),
            'model_output_tokens': sum(u.get('output_tokens') or 0 for u in usage),
            'unknown_usage_records': sum(u.get('usage_source') != 'actual' for u in usage),
        }
        by_type = collections.defaultdict(lambda: [0, 0])
        for t in trials:
            for kind in questions[t['question_id']]['types']:
                by_type[kind][0] += t['score']['covered']
                by_type[kind][1] += t['score']['total']
        summary[variant]['coverage_by_type'] = {k: f'{a}/{b}' for k, (a, b) in sorted(by_type.items())}

    per_question = collections.defaultdict(dict)
    for variant, trials in by_variant.items():
        for t in trials:
            per_question[t['question_id']].setdefault(variant, []).append(
                f"{t['score']['covered']}/{t['score']['total']}")

    probes = [r for r in rows if r['question_id'] not in questions]
    (output / 'scores.json').write_text(json.dumps({
        'questions_sha256': __import__('hashlib').sha256(questions_file.read_bytes()).hexdigest(),
        'summary': summary, 'per_question': per_question,
        'trials': [{k: r[k] for k in ('variant', 'question_id', 'round', 'stop_reason', 'score')}
                   for v in by_variant.values() for r in v],
        'probes': [{k: r[k] for k in ('variant', 'question_id', 'round', 'stop_reason', 'tool_calls', 'answer')}
                   for r in probes]}, indent=2, ensure_ascii=False) + '\n')

    variants = sorted(by_variant)
    lines = ['# Benchmark v2 scores', '', 'Page-identity scores, not answer accuracy. Content is reviewed separately.', '',
             '| Variant | Facet coverage | Complete questions | Citation precision | Pages cited / trial | '
             'Partial fallbacks | Zero-citation | Median / p95 s |', '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for v in variants:
        x = summary[v]
        lines.append(f"| {v} | {x['facet_coverage']} ({x['facet_coverage_rate']:.0%}) | "
                     f"{x['complete_questions']}/{x['trials_with_course_facets']} | {x['citation_precision']:.0%} | "
                     f"{x['cited_pages_per_trial']} | {x['partial_fallbacks']} | {x['zero_citation_trials']} | "
                     f"{x['median_seconds']} / {x['p95_seconds']} |")
    lines += ['', '## Coverage by question type', '', '| Type | ' + ' | '.join(variants) + ' |',
              '| --- |' + ' ---: |' * len(variants)]
    kinds = sorted({k for v in variants for k in summary[v]['coverage_by_type']})
    for kind in kinds:
        lines.append(f'| {kind} | ' + ' | '.join(summary[v]['coverage_by_type'].get(kind, '-') for v in variants) + ' |')
    lines += ['', '## Per question (facets covered, round 1 / round 2)', '',
              '| Question | ' + ' | '.join(variants) + ' |', '| --- |' + ' ---: |' * len(variants)]
    for qid in sorted(per_question):
        lines.append(f'| {qid} | ' + ' | '.join(' / '.join(per_question[qid].get(v, ['-'])) for v in variants) + ' |')
    (output / 'SCORES.md').write_text('\n'.join(lines) + '\n')

    side = ['# Benchmark v2 side by side', '', 'Verbatim answers for content review. Facet marks are page identity only.', '']
    for qid in sorted(per_question):
        q = questions[qid]
        side += [f'## {qid} ({q["split"]})', '', f'> {q["question"]}', '']
        side += [f'- {f["facet_id"]} [{f["kind"]}] {f["need"]}' for f in q['facets']]
        for r in sorted((r for v in by_variant.values() for r in v if r['question_id'] == qid),
                        key=lambda r: (r['variant'], r['round'])):
            marks = ' '.join(f"{f['facet_id'].split('.')[1]}{'✓' if f['covered'] else '✗'}" for f in r['score']['facets'])
            side += ['', f"### {r['variant']} round {r['round']} · {r['stop_reason']} · {marks} · "
                     f"cited {sorted(tuple(p) for p in r['cited_pages'])}", '', r['answer'].strip()]
        side.append('')
    (output / 'SIDE_BY_SIDE_V2.md').write_text('\n'.join(side) + '\n')
    print('\n'.join(lines[:4 + len(variants) + 2]))


if __name__ == '__main__':
    main(Path(sys.argv[1]))
