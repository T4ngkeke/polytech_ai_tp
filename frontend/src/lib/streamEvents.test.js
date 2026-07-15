import { test } from 'node:test';
import assert from 'node:assert/strict';

import { interpretStreamEvent } from './streamEvents.js';

// The chat SSE stream opens with `event: status\ndata: {"route": ...}` before any
// token. It must be recognised as a status event, NEVER appended as a content
// token — otherwise `content + JSON.parse('{"route":"exercise"}')` renders the
// literal string "[object Object]" at the head of every assistant reply.
test('a status event is classified as status, not a token', () => {
  const action = interpretStreamEvent({ event: 'status', data: '{"route": "exercise"}' });
  assert.equal(action.type, 'status');
  assert.equal(action.route, 'exercise');
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
