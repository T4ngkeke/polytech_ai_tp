import { test } from 'node:test';
import assert from 'node:assert/strict';

import { basis, failureText, reportFile } from './reports.js';

test('a failed report says why, a written one does not', () => {
  assert.equal(failureText(null), null);
  assert.equal(failureText({ status: 'done' }), null);
  assert.match(failureText({ status: 'failed', error: 'no_questions' }), /No questions yet/);
  assert.match(failureText({ status: 'failed', error: 'interrupted' }), /restarted/);
  assert.match(failureText({ status: 'failed', error: 'TimeoutError' }), /TimeoutError/);
});

test('the basis names the questions, the period and the search matches', () => {
  assert.equal(basis({}), '');
  const facts = { questions: 108, questions_given_to_model: 108, pages_matched_by_search: 24,
    period: { first_question: '2026-10-14T08:56:04Z', last_question: '2026-10-15T12:01:44Z' } };
  assert.equal(basis({ facts }), 'Based on 108 questions from 14 Oct to 15 Oct · 24 tied to pages by a course search');
  assert.equal(basis({ facts: { ...facts, questions: 1, questions_given_to_model: 1, pages_matched_by_search: 0,
    period: { first_question: '2026-10-14T08:00:00Z', last_question: '2026-10-14T09:00:00Z' } } }),
  'Based on 1 question on 14 Oct');
  assert.match(basis({ facts: { ...facts, questions: 200, questions_given_to_model: 150 } }), /the latest 150 given/);
});

test('a report file keeps the date, the basis and the warning above the text', () => {
  const report = { status: 'done', content: '## Topics\n- jointures\n', finished_at: '2026-10-15T09:30:00Z',
    facts: { questions: 3, questions_given_to_model: 3, period: null, pages_matched_by_search: 0 } };
  const file = reportFile(report, { labName: 'TP SQL', studentName: 'élève.1' });
  assert.equal(file.title, 'AI report — TP SQL — élève.1');
  assert.equal(file.name, 'ai-report-tp-sql-élève-1-2026-10-15.md');
  assert.match(file.markdown, /^# AI report — TP SQL — élève\.1\n\nWritten 2026-10-15 · Based on 3 questions\.\n/);
  assert.match(file.markdown, /check it before you rely on it\.\n\n## Topics\n- jointures\n$/);
  assert.equal(reportFile(report).name, 'ai-report-lab-2026-10-15.md');
});
