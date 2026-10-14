import { test } from 'node:test';
import assert from 'node:assert/strict';

import { createRequestId } from './messageId.js';

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

test('a request ID is a v4 UUID even without crypto.randomUUID (plain HTTP)', () => {
  const insecure = { getRandomValues: (array) => globalThis.crypto.getRandomValues(array) };
  const ids = new Set(Array.from({ length: 50 }, () => createRequestId(insecure)));
  assert.equal(ids.size, 50);
  for (const id of ids) assert.match(id, UUID_V4);
});

test('crypto.randomUUID is used when the page is a secure context', () => {
  assert.equal(createRequestId({ randomUUID: () => 'from-random-uuid' }), 'from-random-uuid');
});
