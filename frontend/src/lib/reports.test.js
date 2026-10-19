import { test } from 'node:test';
import assert from 'node:assert/strict';

import { basis, failureText } from './reports.js';

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
