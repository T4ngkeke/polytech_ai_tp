import { test } from 'node:test';
import assert from 'node:assert/strict';

import { reportMarkdown } from './citationReport.js';

const report = {
  period: { start: '2026-10-01T00:00:00+00:00', end: '2026-10-15T00:00:00+00:00' },
  totals: { questions: 4, students: 2, answers_with_sources: 3, unverified: 1, marked_wrong: 1 },
  files: [{
    document_id: 'd', filename: 'SQL.pdf', in_lab: true, answers: 3, students: 2, share: 1,
    pages: [{ pdf_page_no: 28, answers: 2, students: 2,
      passages: [{ block_id: 'b', kind: 'text', excerpt: 'Clause HAVING | groupes', answers: 2 }],
      examples: [{ student: 'alice', student_id: 'u1', question: 'HAVING ?', asked_at: '2026-10-14T09:00:00+00:00' }] }],
  }],
  uncited_files: [{ document_id: 'e', filename: 'Graphes.pdf' }],
  unverified: [{ student: 'bob', question: 'Et le TP 3 ?', asked_at: '2026-10-13T08:00:00+00:00', outcome: 'partial_deadline_exceeded' }],
  marked_wrong: [{ student: 'alice', question: 'HAVING ?', asked_at: '2026-10-14T09:00:00+00:00',
    pages: [{ filename: 'SQL.pdf', pdf_page_no: 28 }] }],
};

test('the export keeps every number of the report and names who asked', () => {
  const md = reportMarkdown(report, 'TP SQL');
  assert.match(md, /^# Course use — TP SQL\n/);
  assert.match(md, /Period: 2026-10-01 to 2026-10-14\./);
  assert.match(md, /- Questions: 4 from 2 student\(s\)/);
  assert.match(md, /\| SQL\.pdf \| 3 \| 100% \| 2 \| p\. 28 \(2\) \|/);
  // A pipe inside a cell would break the table.
  assert.match(md, /"Clause HAVING \\\| groupes" \(2\)/);
  assert.match(md, /- alice, 2026-10-14: HAVING \?/);
  assert.match(md, /## Files no answer cited\n\n- Graphes\.pdf/);
  assert.match(md, /- bob, 2026-10-13: Et le TP 3 \? \(partial_deadline_exceeded\)/);
  assert.match(md, /cited SQL\.pdf p\. 28/);
});

test('an empty period reads as all time and has no empty sections', () => {
  const md = reportMarkdown({ ...report, period: { start: null, end: null }, files: [],
    uncited_files: [], unverified: [], marked_wrong: [] });
  assert.match(md, /Period: all time\./);
  assert.doesNotMatch(md, /## Files no answer cited|## Questions without|## Answers marked wrong/);
});
