import { test } from 'node:test';
import assert from 'node:assert/strict';

import { autoName } from './rules.js';

test('a saved rule is named after its first four words', () => {
  assert.equal(autoName('Use C.'), 'Use C');
  assert.equal(autoName('  Answer with an example from the TP subject first. '), 'Answer with an example…');
});

test('a conflict is shown while both rules are on, and keyed by their texts', async () => {
  const { conflictKey, conflictNumbers, liveConflicts, loadIgnored, saveIgnored } = await import('./rules.js');
  const items = [{ id: 'a', text: 'Answer in French.', is_active: true },
    { id: 'b', text: 'Off.', is_active: false },
    { id: 'c', text: 'Answer in English.', is_active: true }];
  const conflict = { rule_ids: ['c', 'a'], reason: 'French or English.' };
  assert.deepEqual(liveConflicts([conflict], items), [conflict]);
  assert.deepEqual(conflictNumbers(items, conflict), [1, 3]);
  assert.deepEqual(liveConflicts([conflict], [items[0], { ...items[2], is_active: false }]), []);
  assert.deepEqual(liveConflicts([conflict], [items[0]]), []);
  const key = conflictKey(items, conflict);
  assert.equal(key, conflictKey([...items].reverse(), { rule_ids: ['a', 'c'] }));
  assert.notEqual(key, conflictKey([{ ...items[0], text: 'Answer in German.' }, items[2]], conflict));
  const store = {};
  const storage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } };
  assert.deepEqual(loadIgnored('lab:1', storage), []);
  saveIgnored('lab:1', [key], storage);
  assert.deepEqual(loadIgnored('lab:1', storage), [key]);
  assert.deepEqual(loadIgnored('lab:2', storage), []);
  assert.deepEqual(loadIgnored('lab:1', { getItem: () => { throw new Error('blocked'); } }), []);
});
