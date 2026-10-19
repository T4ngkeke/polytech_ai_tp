import { test } from 'node:test';
import assert from 'node:assert/strict';

import { DEFAULT_MODE, modeLabel, offersResearch, saveMode, storedMode } from './answerModes.js';

function memory() {
  const items = {};
  return () => ({ getItem: (k) => items[k] ?? null, setItem: (k, v) => { items[k] = v; } });
}

test('the mode is remembered per session, research by default', () => {
  const storage = memory();
  assert.equal(storedMode('s1', storage), DEFAULT_MODE);
  saveMode('s1', 'search', storage);
  assert.equal(storedMode('s1', storage), 'search');
  assert.equal(storedMode('s2', storage), 'research');
  assert.equal(storedMode(null, storage), 'research');
});

test('an unknown stored value or blocked storage falls back to research', () => {
  const storage = memory();
  saveMode('s1', 'turbo', storage);
  assert.equal(storedMode('s1', storage), 'research');
  assert.equal(storedMode('s1', () => { throw new Error('SecurityError'); }), 'research');
  assert.doesNotThrow(() => saveMode('s1', 'search', () => { throw new Error('SecurityError'); }));
});

test('labels name the mode; older answers have none', () => {
  assert.match(modeLabel('direct'), /not consulted/);
  assert.equal(modeLabel('search'), 'Search');
  assert.equal(modeLabel(null), null);
});

test('only an uncited search answer offers research', () => {
  const answer = { sender: 'llm', answer_mode: 'search', content: 'Pas trouvé.', citations: [] };
  assert.equal(offersResearch(answer), true);
  assert.equal(offersResearch({ ...answer, citations: [{ evidence_id: 'E1' }] }), false);
  assert.equal(offersResearch({ ...answer, answer_mode: 'research' }), false);
  assert.equal(offersResearch({ ...answer, content: '' }), false);
  assert.equal(offersResearch({ ...answer, sender: 'user' }), false);
});
