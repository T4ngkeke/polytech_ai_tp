import { test } from 'node:test';
import assert from 'node:assert/strict';

import { buildTurns, processSummary } from './turns.js';

const q = (id, extra = {}) => ({ id, sender: 'user', content: id, ...extra });
const a = (id) => ({ id, sender: 'llm', content: id });

test('questions and answers pair up; versions of the last question group', () => {
  const turns = buildTurns([q('q1'), a('a1'), q('q2', { superseded_at: 'x' }), a('a2'),
    q('q2b', { version_of: 'q2' }), a('a2b')]);
  assert.equal(turns.length, 2);
  assert.equal(turns[1].root, 'q2');
  assert.deepEqual(turns[1].versions.map((v) => [v.question.id, v.answer.id]), [['q2', 'a2'], ['q2b', 'a2b']]);
  assert.equal(turns[1].current, 1);
});

test('a question still waiting has no answer; a lone answer is its own turn', () => {
  const turns = buildTurns([a('old'), q('q1')]);
  assert.equal(turns[0].versions[0].question, null);
  assert.equal(turns[1].versions[0].answer, null);
});

test('the summary counts searches, pages, web and Python', () => {
  const s = processSummary({ steps: [{ tool: 'search' }, { tool: 'read', file: 'a.pdf', page: 4 },
    { tool: 'read', file: 'a.pdf', page: 4 }, { tool: 'search_web' }, { tool: 'execute_code' }],
  started_at: 1000, finished_at: 9200, course_use: 'cited' });
  assert.deepEqual(s, { searched: true, pages: 1, web: 1, code: 1, seconds: 8, courseUse: 'cited' });
  assert.equal(processSummary({ course_use: 'none' }).searched, false);
});
