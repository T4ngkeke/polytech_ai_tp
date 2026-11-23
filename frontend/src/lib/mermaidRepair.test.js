import { test } from 'node:test';
import assert from 'node:assert/strict';
import { repairMermaid } from './mermaidRepair.js';

test('labels with parentheses are quoted in a flowchart', () => {
  const source = 'graph TD\n    A[fib(5)] --> B[fib(4)]\n    A --> C{n == 0 (base)?}\n    style C fill:#f9f';
  assert.equal(repairMermaid(source),
    'graph TD\n    A["fib(5)"] --> B["fib(4)"]\n    A --> C{"n == 0 (base)?"}\n    style C fill:#f9f');
});

test('plain and quoted labels stay as they are', () => {
  const source = 'flowchart LR\n  A[Start] --> B["fib(3)"]\n  B --> C{ok?}';
  assert.equal(repairMermaid(source), source);
});

test('other diagram types are not touched', () => {
  const source = 'classDiagram\n  class Animal {\n    +crier() void\n  }';
  assert.equal(repairMermaid(source), source);
});
