import { test } from 'node:test';
import assert from 'node:assert/strict';

import { storedTheme } from './theme.js';

const storage = (value) => () => ({ getItem: () => value });

test('light is the default', () => {
  assert.equal(storedTheme(storage(null)), 'light');
  assert.equal(storedTheme(storage('something else')), 'light');
});

test('a stored dark choice is kept', () => {
  assert.equal(storedTheme(storage('dark')), 'dark');
});

test('blocked storage falls back to light', () => {
  assert.equal(storedTheme(() => { throw new Error('SecurityError'); }), 'light');
});
