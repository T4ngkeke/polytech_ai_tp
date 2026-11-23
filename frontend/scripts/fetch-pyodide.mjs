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

const VERSION = '0.26.4';
const BASE = `https://cdn.jsdelivr.net/pyodide/v${VERSION}/full/`;
const CORE = {
  'pyodide.js': 'c0069107621d5b942a659e737a12e774cc0451feaa2256f475d72e071d844ec7',
  'pyodide.asm.js': '919560652ed3dad3707cb3a394785da1e046fb13dc0defa162058ff230cb7eed',
  'pyodide.asm.wasm': 'b7e66a19427a55010ac3367c1b6c64b893f9826f783412945fdf0c3337f3bc94',
  'python_stdlib.zip': '72894522b791858b9d613ac786b951d8b5094035dcf376313ea24a466810f336',
  'pyodide-lock.json': 'cd50b49de944c579045e122fe8628b31f9ce446379f032f36c05e273d38766e0',
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
