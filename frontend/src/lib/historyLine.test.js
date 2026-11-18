import { test } from 'node:test';
import assert from 'node:assert/strict';

import { historyLineIndex, messageLength, MAX_MESSAGE_CHARS } from './historyLine.js';

const messages = [{ id: 'u1' }, { id: 'a1' }, { id: 'u2' }, { id: 'a2' }, { id: 'tmp-u3' }, { id: 'a3' }];

test('no line while everything is sent', () => {
  assert.deepEqual(historyLineIndex(messages, { seen_from: null, hidden: 0 }), { index: -1, missing: false });
  assert.deepEqual(historyLineIndex(messages, null), { index: -1, missing: false });
});

test('the line sits before the first message still sent', () => {
  assert.deepEqual(historyLineIndex(messages, { seen_from: 'u2', hidden: 2 }), { index: 2, missing: false });
});

test('nothing sent any more: the line goes after the last message', () => {
  assert.deepEqual(historyLineIndex(messages, { seen_from: null, hidden: 6 }), { index: 6, missing: false });
});

test('a message known only by its temporary id asks for a reload', () => {
  assert.deepEqual(historyLineIndex(messages, { seen_from: 'u3', hidden: 4 }), { index: -1, missing: true });
});

test('the count shows near the limit and the box refuses past it', () => {
  assert.deepEqual(messageLength('hello'), { over: false, near: false, count: '', excess: 0 });
  const near = messageLength('x'.repeat(17500));
  assert.equal(near.count, '17,500 / 20,000');
  assert.equal(near.over, false);
  const over = messageLength('x'.repeat(MAX_MESSAGE_CHARS + 1));
  assert.equal(over.over, true);
  assert.equal(over.excess, 1);
});
