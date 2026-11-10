import { test } from 'node:test';
import assert from 'node:assert/strict';

import { autoName } from './rules.js';

test('a saved rule is named after its first four words', () => {
  assert.equal(autoName('Use C.'), 'Use C');
  assert.equal(autoName('  Answer with an example from the TP subject first. '), 'Answer with an example…');
});
