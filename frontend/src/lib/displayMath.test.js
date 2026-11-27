import { test } from 'node:test';
import assert from 'node:assert/strict';

import { displayMathOnOwnLines } from './displayMath.js';

test('a one-line display formula is split onto its own lines', () => {
  const text = 'Carte :\n\n$$\\mathfrak{C}_{1,m}[k] = \\sum_{p} h[p] \\tag{11}$$\n\nc’est-à-dire';
  assert.equal(displayMathOnOwnLines(text),
    'Carte :\n\n$$\n\\mathfrak{C}_{1,m}[k] = \\sum_{p} h[p] \\tag{11}\n$$\n\n\nc’est-à-dire');
});

test('a trailing space and a formula right under a sentence', () => {
  assert.equal(displayMathOnOwnLines('Récurrence :\n$$a = b \\tag{12}$$ \n[E97].'),
    'Récurrence :\n\n$$\na = b \\tag{12}\n$$\n\n[E97].');
});

test('formulas inside sentences, already split, or in code stay as they are', () => {
  const kept = [
    'La somme $$a+b$$ vaut c.',
    'Le cours note $x$ et $y$.',
    '$$\na = b \\tag{1}\n$$',
    '```latex\n$$a = b$$\n```',
    '~~~\n$$a$$\n~~~',
    '$$$$',
    '$$ $$',
    'prix 5$$ et 6$$',
  ];
  for (const text of kept) assert.equal(displayMathOnOwnLines(text), text, text);
});

test('a formula in a list item keeps its indent', () => {
  assert.equal(displayMathOnOwnLines('- point\n  $$x = 1$$'), '- point\n\n  $$\n  x = 1\n  $$\n');
});
