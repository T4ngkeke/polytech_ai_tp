import { test } from 'node:test';
import assert from 'node:assert/strict';

import { modeAfterScroll, scrollTarget } from './chatScroll.js';

const view = { scrollHeight: 3000, clientHeight: 800 }; // bottom = 2200

test('pin follows a short answer to the bottom', () => {
  assert.equal(scrollTarget('pin', { ...view, latestTop: 2500 }), 2200);
});

test('pin stops when the answer start reaches the top', () => {
  // The answer starts at 1000: a longer answer keeps its first line in view.
  assert.equal(scrollTarget('pin', { ...view, latestTop: 1000 }), 988);
  assert.equal(scrollTarget('pin', { scrollHeight: 6000, clientHeight: 800, latestTop: 1000 }), 988);
});

test('tail follows the newest text; off stays', () => {
  assert.equal(scrollTarget('tail', { ...view, latestTop: 1000 }), 2200);
  assert.equal(scrollTarget('off', { ...view, latestTop: 1000 }), null);
  assert.equal(scrollTarget('pin', { ...view, latestTop: null }), 2200);
});

test('scrolling by hand: to the bottom follows again, elsewhere stops', () => {
  assert.equal(modeAfterScroll({ scrollTop: 2190, scrollHeight: 3000, clientHeight: 800 }), 'tail');
  assert.equal(modeAfterScroll({ scrollTop: 900, scrollHeight: 3000, clientHeight: 800 }), 'off');
});
