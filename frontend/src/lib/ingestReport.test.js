import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ingestReportWarnings } from './ingestReport.js';

test('no report → no warnings', () => {
  assert.deepEqual(ingestReportWarnings(null), []);
  assert.deepEqual(ingestReportWarnings(undefined), []);
});

test('a clean run produces no warnings', () => {
  const w = ingestReportWarnings({
    chunk_count: 12, ingest_tokens_est: 400, token_budget: 200000,
    over_budget: false,
  });
  assert.deepEqual(w, []);
});

test('an over-budget report surfaces the estimate and the budget', () => {
  const w = ingestReportWarnings({
    chunk_count: 10, ingest_tokens_est: 5000, token_budget: 1000, over_budget: true,
  });
  assert.equal(w.length, 1);
  assert.match(w[0], /5000/);
  assert.match(w[0], /1000/);
});

test('repaired formula regions ask the teacher to check them', () => {
  const w = ingestReportWarnings({
    chunk_count: 3,
    vlm_repairs: [
      { status: 'replaced', before: '215', after: '2^{15}' },
      { status: 'replaced', before: '211', after: '2^{11}' },
    ],
  });
  assert.equal(w.length, 1);
  assert.match(w[0], /2 /);
  assert.match(w[0], /vision model|verify/i);
});

test('regions that could not be repaired are reported separately', () => {
  const w = ingestReportWarnings({
    vlm_repairs: [
      { status: 'replaced' },
      { status: 'unreadable' },
      { status: 'refused_invented_text' },
    ],
  });
  assert.equal(w.length, 2);
  assert.ok(w.some((x) => /could not be repaired/i.test(x)));
});

// The segmentation half of the report is gone with the Exercises table. A stale
// backend still emitting those keys must not resurrect warnings about numbering
// that no longer means anything.
test('leftover segmentation keys drive no warnings', () => {
  const w = ingestReportWarnings({
    segmenter: 'regex_fallback',
    anomaly: 'duplicate_numbers',
    gaps: [4, 7],
    collisions: ['3'],
    segmentation_disagreement: true,
    exercise_count: 5,
  });
  assert.deepEqual(w, []);
});
