import { test } from 'node:test';
import assert from 'node:assert/strict';

import { TOURS, boxPosition } from './tours.js';

const VIEW = { width: 1280, height: 800 };
const BOX = { width: 320, height: 160 };

test('the box goes in the middle when the element is not on screen', () => {
  assert.deepEqual(boxPosition(null, BOX, VIEW), { top: 320, left: 480, width: 320 });
});

test('the box goes below, else above, else beside the element', () => {
  assert.deepEqual(boxPosition({ top: 100, left: 50, width: 200, height: 40 }, BOX, VIEW), { top: 152, left: 50, width: 320 });
  assert.equal(boxPosition({ top: 700, left: 50, width: 200, height: 40 }, BOX, VIEW).top, 700 - 12 - 160);
  const tall = boxPosition({ top: 100, left: 20, width: 200, height: 650 }, BOX, VIEW);
  assert.equal(tall.left, 20 + 200 + 12);
});

test('the box stays inside a phone screen', () => {
  const phone = { width: 390, height: 844 };
  const pos = boxPosition({ top: 100, left: 300, width: 80, height: 30 }, BOX, phone);
  assert.ok(pos.left >= 12 && pos.left + pos.width <= phone.width - 12);
});

test('every step has a title and a text, and each tour ends on the help button', () => {
  for (const steps of Object.values(TOURS)) {
    assert.ok(steps.every((s) => s.title && s.text));
    assert.equal(steps[steps.length - 1].target, 'help');
  }
});
