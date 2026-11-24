import { test } from 'node:test';
import assert from 'node:assert/strict';
import { needsReload, shownQuestions } from './conversationSync.js';

const q = (id, extra = {}) => ({ id, sender: 'user', ...extra });
const a = (id) => ({ id, sender: 'llm' });

test('a page in step with the server does not load again', () => {
  const page = [q('q1'), a('a1'), q('q2'), a('a2')];
  assert.equal(needsReload({ running: false, questions: 2, last_question_id: 'q2' }, page, false), false);
});

test('a question asked in another window is loaded', () => {
  const page = [q('q1'), a('a1')];
  assert.equal(needsReload({ running: true, questions: 2, last_question_id: 'q2' }, page, false), true);
});

test('an answer finished elsewhere is loaded once the run ends', () => {
  const page = [q('q1'), a('a1'), q('q2')];
  assert.equal(needsReload({ running: false, questions: 2, last_question_id: 'q2' }, page, true), true);
  assert.equal(needsReload({ running: true, questions: 2, last_question_id: 'q2' }, page, true), false);
});

test('a new version made elsewhere is loaded (same number of questions)', () => {
  const page = [q('q1'), a('a1')];
  assert.equal(needsReload({ running: false, questions: 1, last_question_id: 'q1b' }, page, false), true);
});

test('replaced versions and unsaved questions are not counted', () => {
  const page = [q('q1', { superseded_at: '2026-11-24' }), q('q1b'), q('local-x')];
  assert.deepEqual(shownQuestions(page).map((m) => m.id), ['q1b']);
});
