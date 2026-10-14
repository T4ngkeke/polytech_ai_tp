import { test } from 'node:test';
import assert from 'node:assert/strict';

import { applyToMessage } from './streamMessage.js';

test('a draft grows, is withdrawn, and the checked answer replaces it', () => {
  let m = { id: 'a', sender: 'llm', content: '' };
  m = applyToMessage(m, { type: 'draft', text: 'Je ' });
  m = applyToMessage(m, { type: 'draft', text: 'regarde' });
  assert.equal(m.draft, 'Je regarde');
  m = applyToMessage(m, { type: 'draft_reset' });
  assert.equal(m.draft, '');
  m = applyToMessage(m, { type: 'draft', text: 'Vin3 [E1]' });
  m = applyToMessage(m, { type: 'token', text: 'Vin3 [E1].' });
  assert.deepEqual([m.content, m.draft], ['Vin3 [E1].', '']);
});

test('citations and a question back are kept; other actions change nothing', () => {
  const m = { id: 'a', sender: 'llm', content: 'Quel cours ?' };
  const clarification = { question: 'Quel cours ?', options: ['SQL', 'Graphes'] };
  assert.deepEqual(applyToMessage(m, { type: 'clarification', clarification }).clarification, clarification);
  assert.deepEqual(applyToMessage(m, { type: 'citations', citations: [] }).citations, []);
  assert.equal(applyToMessage(m, { type: 'status', phase: 'reading' }), m);
});
