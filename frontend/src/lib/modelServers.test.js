import { test } from 'node:test';
import assert from 'node:assert/strict';

import { contextLength, serverState } from './modelServers.js';

test('a wrong model name says what the server has instead', () => {
  assert.deepEqual(serverState({ status: 'ok' }), { text: 'served', tone: 'ok' });
  assert.deepEqual(serverState({ status: 'model_missing', served: ['flash-next'] }),
    { text: 'not served — the server has: flash-next', tone: 'bad' });
  assert.equal(serverState({ status: 'unreachable' }).tone, 'warn');
  assert.equal(serverState({ status: 'off' }).text, 'off');
});

test('the context length reads in tokens and in k', () => {
  assert.equal(contextLength(65536), '65,536 tokens (64k)');
  assert.equal(contextLength(null), '—');
});
