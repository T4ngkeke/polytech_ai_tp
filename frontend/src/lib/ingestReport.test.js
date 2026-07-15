import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ingestReportWarnings } from './ingestReport.js';

test('no report → no warnings', () => {
  assert.deepEqual(ingestReportWarnings(null), []);
  assert.deepEqual(ingestReportWarnings(undefined), []);
});

test('a clean llm segmentation produces no warnings', () => {
  const w = ingestReportWarnings({
    segmenter: 'llm', boundary_disagreements: 0, dropped_invalid_lines: 0,
    anomaly: null, gaps: [], collisions: [], exercise_count: 5,
  });
  assert.deepEqual(w, []);
});

// The backend renamed the old `resegmented` flag to `segmenter`; a classifier
// outage now surfaces as segmenter === 'regex_fallback'. That degradation must
// be visible to the teacher (it silently disappeared after the rename).
test('regex_fallback (classifier failed) surfaces a degradation warning', () => {
  const w = ingestReportWarnings({ segmenter: 'regex_fallback', gaps: [], collisions: [] });
  assert.equal(w.length, 1);
  assert.match(w[0], /regex|classifier|fell back/i);
});

test('anomaly, gaps and collisions each surface a warning', () => {
  const w = ingestReportWarnings({
    segmenter: 'llm', anomaly: 'duplicate_numbers', gaps: [4, 7], collisions: ['3'],
  });
  assert.equal(w.length, 3);
  assert.ok(w.some((x) => /duplicate_numbers/.test(x)));
  assert.ok(w.some((x) => /4, 7/.test(x)));
  assert.ok(w.some((x) => /3/.test(x)));
});

test('a CM over-budget report surfaces the estimate', () => {
  const w = ingestReportWarnings({
    chunk_count: 10, ingest_tokens_est: 5000, token_budget: 1000, over_budget: true,
  });
  assert.equal(w.length, 1);
  assert.match(w[0], /5000/);
  assert.match(w[0], /1000/);
});

test('the removed `resegmented` key no longer drives a warning', () => {
  // A stale backend that still emitted `resegmented` would not, but the current
  // shape never sets it; a report with only resegmented=true stays quiet.
  const w = ingestReportWarnings({ resegmented: true, gaps: [], collisions: [] });
  assert.deepEqual(w, []);
});
