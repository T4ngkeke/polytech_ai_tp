import { test } from 'node:test';
import assert from 'node:assert/strict';

import { interpretStreamEvent, openStream, streamRefusal } from './streamEvents.js';

// The chat SSE stream opens with `event: status\ndata: {"route": ...}` before any
// token. It must be recognised as a status event, NEVER appended as a content
// token — otherwise `content + JSON.parse('{"route":"rag"}')` renders the
// literal string "[object Object]" at the head of every assistant reply.
test('a status event is classified as status, not a token', () => {
  const action = interpretStreamEvent({ event: 'status', data: '{"route": "rag"}' });
  assert.equal(action.type, 'status');
  assert.equal(action.route, 'rag');
});

test('status with a null route does not become a token', () => {
  const action = interpretStreamEvent({ event: 'status', data: '{"route": null}' });
  assert.equal(action.type, 'status');
  assert.equal(action.route, null);
});

test('a content token frame (no event name) decodes the JSON-encoded string', () => {
  const action = interpretStreamEvent({ data: '"hello\\nworld"' });
  assert.equal(action.type, 'token');
  assert.equal(action.text, 'hello\nworld');
});

test('a non-JSON token frame falls back to the raw string', () => {
  const action = interpretStreamEvent({ data: 'plain text' });
  assert.equal(action.type, 'token');
  assert.equal(action.text, 'plain text');
});

test('done and error are recognised with a decoded detail', () => {
  assert.equal(interpretStreamEvent({ event: 'done', data: '{}' }).type, 'done');
  const err = interpretStreamEvent({ event: 'error', data: '{"detail": "boom"}' });
  assert.equal(err.type, 'error');
  assert.equal(err.detail, 'boom');
});

test('citations are parsed to an array; malformed → empty array', () => {
  const ok = interpretStreamEvent({ event: 'citations', data: '[{"filename":"a.pdf"}]' });
  assert.equal(ok.type, 'citations');
  assert.deepEqual(ok.citations, [{ filename: 'a.pdf' }]);
  const bad = interpretStreamEvent({ event: 'citations', data: 'not json' });
  assert.deepEqual(bad.citations, []);
});

test('an unknown named event without data is ignored, never a token', () => {
  assert.equal(interpretStreamEvent({ event: 'ping' }).type, 'ignore');
});

test('a new conversation receives its title after done; a bad title is ignored', () => {
  assert.deepEqual(interpretStreamEvent({ event: 'title', data: '{"session_id": "s-1", "title": "Jointure naturelle en SQL"}' }),
    { type: 'title', sessionId: 's-1', title: 'Jointure naturelle en SQL' });
  assert.equal(interpretStreamEvent({ event: 'title', data: '{"title": ""}' }).type, 'ignore');
  assert.equal(interpretStreamEvent({ event: 'title', data: 'oops' }).type, 'ignore');
});

test('a research status carries its phase; done carries the saved message id', () => {
  const status = interpretStreamEvent({ event: 'status', data: '{"route": "agent", "phase": "reading"}' });
  assert.equal(status.phase, 'reading');
  assert.equal(interpretStreamEvent({ event: 'status', data: '{"route": "rag"}' }).phase, null);
  const done = interpretStreamEvent({ event: 'done', data: '{"message_id": "m-1", "user_message_id": "u-1"}' });
  assert.equal(done.messageId, 'm-1');
  assert.equal(done.userMessageId, 'u-1');
  assert.equal(interpretStreamEvent({ event: 'done', data: '{}' }).messageId, null);
  assert.equal(interpretStreamEvent({ event: 'done', data: '{}' }).userMessageId, null);
});

test('draft text and its withdrawal are never content tokens', () => {
  assert.deepEqual(interpretStreamEvent({ event: 'draft', data: '"Bonjour\\n"' }), { type: 'draft', text: 'Bonjour\n' });
  assert.deepEqual(interpretStreamEvent({ event: 'draft_reset', data: '{}' }), { type: 'draft_reset' });
});

test('a question back carries its string options, never as a token', () => {
  const action = interpretStreamEvent({ event: 'clarification',
    data: '{"question": "Quel cours ?", "options": ["SQL", 3, "Graphes"]}' });
  assert.deepEqual(action, { type: 'clarification', clarification: { question: 'Quel cours ?', options: ['SQL', 'Graphes'] } });
  assert.deepEqual(interpretStreamEvent({ event: 'clarification', data: 'oops' }).clarification.options, []);
});

test('a status says whether the class is in exam mode', () => {
  const action = interpretStreamEvent({ event: 'status',
    data: '{"route": "agent", "phase": "thinking", "exam_mode": true}' });
  assert.equal(action.examMode, true);
  const older = interpretStreamEvent({ event: 'status', data: '{"route": "agent"}' });
  assert.equal(older.examMode, false);
});

test('a question refused before the stream says why and what to reload', async () => {
  assert.deepEqual(streamRefusal(403, 'This lab is locked or no longer available'),
    { message: 'This lab is locked or no longer available', reload: 'classes' });
  assert.equal(streamRefusal(429, 'Daily token quota exceeded').reload, 'usage');
  assert.equal(streamRefusal(404, 'Session not found').reload, 'classes');
  assert.equal(streamRefusal(502, null).reload, null);

  await openStream(new Response('', { status: 200, headers: { 'content-type': 'text/event-stream' } }));
  await assert.rejects(
    openStream(new Response(JSON.stringify({ detail: 'You are not a member of the class that owns this lab' }),
      { status: 403, headers: { 'content-type': 'application/json' } })),
    (err) => err.refusal.message === 'You are not a member of the class that owns this lab' && err.refusal.reload === 'classes');
});

test('steps, tasks, code, follow-ups and the done fields are read', () => {
  assert.deepEqual(interpretStreamEvent({ event: 'step', data: '{"tool":"read","file":"a.pdf","page":4}' }).step,
    { tool: 'read', file: 'a.pdf', page: 4 });
  assert.equal(interpretStreamEvent({ event: 'step', data: '{}' }).type, 'ignore');
  assert.deepEqual(interpretStreamEvent({ event: 'tasks', data: '[{"id":"1"}]' }).tasks, [{ id: '1' }]);
  const code = interpretStreamEvent({ event: 'code', data: '{"id":"r1","code":"print(1)"}' });
  assert.deepEqual([code.type, code.id, code.code], ['code', 'r1', 'print(1)']);
  const follow = interpretStreamEvent({ event: 'follow_ups', data: '{"message_id":"m","follow_ups":["a",2]}' });
  assert.deepEqual(follow.followUps, ['a']);
  const done = interpretStreamEvent({ event: 'done', data: '{"message_id":"m","course_use":"cited","web_sources":[{"id":"W1"}],"code_runs":null}' });
  assert.deepEqual([done.courseUse, done.webSources, done.codeRuns], ['cited', [{ id: 'W1' }], null]);
  assert.deepEqual(interpretStreamEvent({ event: 'question', data: '{"user_message_id":"q1"}' }),
    { type: 'question', userMessageId: 'q1' });
  assert.equal(interpretStreamEvent({ event: 'question', data: '{}' }).type, 'ignore');
});

test('a question changed in another window reloads the conversation', () => {
  for (const detail of ['not_latest_question', 'message_not_found']) {
    const refusal = streamRefusal(detail === 'message_not_found' ? 404 : 409, detail);
    assert.equal(refusal.reload, 'conversation');
    assert.equal(refusal.messageKey, 'chat.changedElsewhere');
  }
});

test('a server Python run is shown, then its output', () => {
  const code = interpretStreamEvent({ event: 'code', data: '{"id": "r1", "code": "print(1)", "where": "server"}' });
  assert.deepEqual(code, { type: 'code', id: 'r1', code: 'print(1)', server: true });
  assert.equal(interpretStreamEvent({ event: 'code', data: '{"id": "r2", "code": "x", "where": "browser"}' }).server, false);
  const out = interpretStreamEvent({ event: 'code_output', data: '{"id": "r1", "output": {"stdout": "1\\n", "stderr": "", "result": ""}}' });
  assert.deepEqual(out, { type: 'code_output', id: 'r1', output: { status: 'ok', stdout: '1\n', stderr: '', result: '' } });
  const failed = interpretStreamEvent({ event: 'code_output', data: '{"id": "r1", "output": {"error": "The Python runner is not available right now."}}' });
  assert.equal(failed.output.status, 'error');
  assert.match(failed.output.stderr, /not available/);
  assert.equal(interpretStreamEvent({ event: 'code_output', data: 'oops' }).type, 'ignore');
});
