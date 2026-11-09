import { test } from 'node:test';
import assert from 'node:assert/strict';

import { EXAM_CLOSED, sourceError } from './sourceError.js';

const failure = (status, message) => Object.assign(new Error(message), { status });

test('a source closed by exam mode says so', () => {
  assert.equal(sourceError(failure(403, 'exam_mode'), 'gone', 'failed'), EXAM_CLOSED);
  assert.equal(sourceError(failure(404, 'not_found'), 'gone', 'failed'), 'gone');
  assert.equal(sourceError(failure(500, 'boom'), 'gone', 'failed'), 'failed');
});
