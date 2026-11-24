// fetch-pyodide.mjs — download Pyodide and the Python packages students may
// import, so the browser loads Python from our own server (public/pyodide-worker.js
// reads /pyodide/), not from a CDN: fast on the LAN, and no internet needed.
//
// Run at image build time (frontend/Dockerfile): node fetch-pyodide.mjs <out dir>.
// Every file is checked: the core files against the hashes below, the packages
// against the sha256 of pyodide-lock.json. The lock written out lists only the
// packages we serve, so importing any other one is a plain ModuleNotFoundError.
import { createHash } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

// 0.29 or later: with 0.26 and 0.27 (Emscripten 3.1.58), Safari 27 draws blank
// matplotlib figures (Agg draws nothing; numpy and Pillow are fine).
const VERSION = '0.29.3';
const BASE = `https://cdn.jsdelivr.net/pyodide/v${VERSION}/full/`;
const CORE = {
  'pyodide.js': '718d40f1c015dd25ec724cc8fc4e2325d6a45a92ae225121ff6953f224a16f72',
  'pyodide.asm.js': '1263f02b5b26099b96112378156f242dd98b39a8201ba7765e5fe3d455c5ce91',
  'pyodide.asm.wasm': 'e2f4ee75b325e35eb31bfb8c613d4dd5098f5502c156a97847686875b5025480',
  'python_stdlib.zip': '4298b6ee445cb724c3973437da47789752b9e6ff4e26619026b283ec801fc46b',
  'pyodide-lock.json': '3256ffc76388de0e37f4b34d42ab484268d1afc675179ff97b2a5bb14f84ccac',
};
// What a course may import (with their dependencies). sqlite3 for SQL exercises.
const PACKAGES = ['numpy', 'matplotlib', 'pandas', 'scipy', 'sympy', 'networkx',
  'scikit-learn', 'statsmodels', 'sqlite3'];

const out = process.argv[2];
if (!out) throw new Error('usage: node fetch-pyodide.mjs <out dir>');
await mkdir(out, { recursive: true });

async function fetchChecked(name, sha256) {
  for (let attempt = 1; ; attempt += 1) {
    try {
      const response = await fetch(BASE + name);
      if (!response.ok) throw new Error(`${name}: HTTP ${response.status}`);
      const body = Buffer.from(await response.arrayBuffer());
      const got = createHash('sha256').update(body).digest('hex');
      if (got !== sha256) throw new Error(`${name}: sha256 ${got}, expected ${sha256}`);
      return body;
    } catch (error) {
      if (attempt >= 3) throw error;
      console.warn(`retrying ${name}: ${error.message}`);
    }
  }
}

for (const [name, sha256] of Object.entries(CORE)) {
  if (name === 'pyodide-lock.json') continue;
  await writeFile(join(out, name), await fetchChecked(name, sha256));
}
const lock = JSON.parse(await fetchChecked('pyodide-lock.json', CORE['pyodide-lock.json']));

const wanted = new Set();
const stack = [...PACKAGES];
while (stack.length) {
  const name = stack.pop();
  if (wanted.has(name)) continue;
  if (!lock.packages[name]) throw new Error(`${name}: not in pyodide-lock.json`);
  wanted.add(name);
  stack.push(...(lock.packages[name].depends || []));
}
let bytes = 0;
for (const name of [...wanted].sort()) {
  const { file_name: file, sha256 } = lock.packages[name];
  const body = await fetchChecked(file, sha256);
  bytes += body.length;
  await writeFile(join(out, file), body);
}
lock.packages = Object.fromEntries(Object.entries(lock.packages).filter(([name]) => wanted.has(name)));
await writeFile(join(out, 'pyodide-lock.json'), JSON.stringify(lock));
console.log(`pyodide ${VERSION}: ${wanted.size} packages, ${(bytes / 1e6).toFixed(1)} MB`);
