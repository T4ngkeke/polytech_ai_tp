import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// The page asks the worker for the Pyodide version the image downloads:
// a mismatch is a 404 on every Python run.
test('python.js and fetch-pyodide.mjs name the same Pyodide version', () => {
  const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
  const page = read('./python.js').match(/PYODIDE_VERSION = '([^']+)'/)?.[1];
  const image = read('../../scripts/fetch-pyodide.mjs').match(/const VERSION = '([^']+)'/)?.[1];
  assert.ok(page);
  assert.equal(page, image);
});
