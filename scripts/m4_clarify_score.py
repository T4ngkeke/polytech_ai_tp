"""Scores of a clarify-benchmark run of scripts/m4_evaluate.py (M4_BENCHMARK=clarify).

Usage: .venv/bin/python scripts/m4_clarify_score.py OUTPUT_DIR

Each first answer is one of:
  asked     a question back: the ask_student tool (stop reason "clarification"),
            or, before that tool existed, a short reply without citations that
            asks a question (under ASKED_TEXT_CHARS characters, with a "?")
  sourced   an answer citing the course
  uncited   an answer without citations that does not ask (a guess)
A follow-up answer is right when it cites one of the question's follow_up_files.
The text rule for "asked" is a heuristic; SIDE_BY_SIDE.md lists every answer
for a check by hand.
"""
from __future__ import annotations

import collections
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / 'documentation/ingestion-agent-acceptance/m4-agent/2026-10-14-clarify-probe/questions.json'
ASKED_TEXT_CHARS = 1200


def outcome(row) -> str:
    if row['stop_reason'] == 'clarification':
        return 'asked'
    if row['cited_pages']:
        return 'sourced'
    answer = row['answer'] or ''
    return 'asked' if '?' in answer and len(answer) <= ASKED_TEXT_CHARS else 'uncited'


def median(values):
    return round(statistics.median(values), 1) if values else None


def main(output: Path):
    questions = {q['id']: q for q in json.loads(QUESTIONS.read_text())['questions']}
    rows = [r for r in json.loads((output / 'results.json').read_text()) if r['question_id'] in questions]
    for row in rows:
        row['outcome'] = outcome(row)
    by_expected = collections.defaultdict(list)
    for row in rows:
        by_expected[questions[row['question_id']]['expected']].append(row)

    summary = {}
    for expected, group in sorted(by_expected.items()):
        counts = collections.Counter(r['outcome'] for r in group)
        summary[expected] = {
            'trials': len(group), **{k: counts.get(k, 0) for k in ('asked', 'sourced', 'uncited')},
            'median_seconds': median([r['elapsed_seconds'] for r in group]),
            'median_seconds_when_asked': median([r['elapsed_seconds'] for r in group if r['outcome'] == 'asked']),
        }
    follow = [r for r in rows if r.get('follow_up')]
    right = [r for r in follow if {f for f, _ in r['follow_up']['cited_pages']}
             & set(questions[r['question_id']]['follow_up_files'])]
    summary['follow_up'] = {'trials': len(follow), 'right_file_cited': len(right),
                            'median_seconds': median([r['follow_up']['elapsed_seconds'] for r in follow])}

    (output / 'clarify_scores.json').write_text(json.dumps({
        'summary': summary,
        'trials': [{k: r[k] for k in ('question_id', 'round', 'outcome', 'stop_reason', 'elapsed_seconds')}
                   for r in rows]}, indent=2) + '\n')
    lines = ['# Clarify benchmark scores', '',
             '| Expected | Trials | Asked | Sourced | Uncited | Median s | Median s when asked |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for expected in ('clarify', 'answer', 'either'):
        x = summary.get(expected)
        if x:
            lines.append(f"| {expected} | {x['trials']} | {x['asked']} | {x['sourced']} | {x['uncited']} | "
                         f"{x['median_seconds']} | {x['median_seconds_when_asked'] or '-'} |")
    f = summary['follow_up']
    lines += ['', f"Follow-ups citing an expected file: {f['right_file_cited']}/{f['trials']} "
                  f"(median {f['median_seconds']} s).", '',
              '| Question | Expected | ' + ' | '.join(f'Round {n}' for n in sorted({r['round'] for r in rows})) + ' |',
              '| --- | --- |' + ' --- |' * len({r['round'] for r in rows})]
    for qid, q in questions.items():
        cells = [f"{r['outcome']} {round(r['elapsed_seconds'])} s"
                 + (' → ' + ('right' if r in right else 'wrong') if r.get('follow_up') else '')
                 for r in sorted((r for r in rows if r['question_id'] == qid), key=lambda r: r['round'])]
        lines.append(f"| {qid} | {q['expected']} | " + ' | '.join(cells) + ' |')
    (output / 'CLARIFY_SCORES.md').write_text('\n'.join(lines) + '\n')

    side = ['# Clarify benchmark side by side', '']
    for qid, q in questions.items():
        side += [f"## {qid} ({q['expected']})", '', f"> {q['q']}", '']
        for r in sorted((r for r in rows if r['question_id'] == qid), key=lambda r: r['round']):
            side += [f"### Round {r['round']} · {r['outcome']} · {r['stop_reason']} · {round(r['elapsed_seconds'])} s · "
                     f"cited {r['cited_pages']}", '']
            if r.get('clarification'):
                side += [f"Options: {r['clarification'].get('options')}", '']
            side += [r['answer'].strip(), '']
            if r.get('follow_up'):
                fu = r['follow_up']
                side += [f"**Follow-up** > {fu['question']} · {round(fu['elapsed_seconds'])} s · cited {fu['cited_pages']}",
                         '', fu['answer'].strip(), '']
    (output / 'SIDE_BY_SIDE.md').write_text('\n'.join(side) + '\n')
    print('\n'.join(lines[:7]))
    print(lines[8])


if __name__ == '__main__':
    main(Path(sys.argv[1]))
