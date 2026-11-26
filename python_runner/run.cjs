'use strict';
// python_runner/run.cjs — one Python run for the research agent, in a fresh
// Pyodide (Python compiled to WebAssembly), as the student's browser runs it
// (frontend/public/pyodide-worker.js, ported from Open WebUI). server.mjs starts
// one such process per run and kills it when it takes too long.
//
// In: one JSON object on stdin, {code}. Out: one JSON line on stdout,
// {stdout, stderr, result}. A matplotlib figure shown with plt.show() is a
// "data:image/png;base64,..." line in stdout, as in the browser.
//
// What the Python code can reach: `import js` is an object with timers only
// (jsglobals), and the pyodide_js module is removed, so it cannot reach Node
// (require, process, the file system). The container adds the rest: no
// network, a read-only file system, a user without rights (docker-compose.yml).
const fs = require('node:fs');
const path = require('node:path');

const DIR = process.env.PYODIDE_DIR || '/pyodide/0.29.3/';

// Pyodide asks process.binding('constants').fs for the file open flags. The
// Node permission model (server.mjs) forbids process.binding: give it the same
// table, and nothing else.
const FS_CONSTANTS = fs.constants;
process.binding = (name) => {
  if (name === 'constants') return { fs: FS_CONSTANTS };
  throw new Error('process.binding is not available');
};
const MAX_OUTPUT = 4 * 1024 * 1024;   // characters of stdout or stderr kept

const PANDAS_QUIET = `
import warnings
warnings.filterwarnings("ignore", message=r"(?s)\\s*Pyarrow will become a required dependency")
`;

const MATPLOTLIB_SHOW = `
import base64
import os
from io import BytesIO
os.environ["MPLBACKEND"] = "AGG"
import matplotlib
matplotlib.use("AGG")
import matplotlib.pyplot
def _edullm_show(*, block=None):
    buf = BytesIO()
    matplotlib.pyplot.savefig(buf, format="png")
    buf.seek(0)
    img_str = base64.b64encode(buf.read()).decode("utf-8")
    matplotlib.pyplot.clf()
    buf.close()
    print(f"data:image/png;base64,{img_str}")
matplotlib.pyplot.show = _edullm_show
`;

function text(value) {
  if (value === undefined || value === null) return '';
  try {
    if (value.toJs) return String(value.toJs());
  } catch { /* not convertible */ }
  return String(value);
}

// The packages the code imports, each after its dependencies, from the local
// files only (the lock lists the packages we serve: frontend/scripts/fetch-pyodide.mjs).
async function loadImports(pyodide, code) {
  const lock = JSON.parse(fs.readFileSync(path.join(DIR, 'pyodide-lock.json'), 'utf8')).packages;
  const byImport = new Map();
  for (const [name, pkg] of Object.entries(lock)) {
    for (const module of pkg.imports || []) byImport.set(module, name);
  }
  const findImports = pyodide.pyimport('pyodide.code').find_imports;
  const found = findImports(code);
  const imports = found.toJs();
  found.destroy();
  findImports.destroy();
  const order = [];
  const seen = new Set();
  const visit = (name) => {
    if (!name || seen.has(name) || !lock[name]) return;
    seen.add(name);
    (lock[name].depends || []).forEach(visit);
    order.push(name);
  };
  imports.forEach((module) => visit(byImport.get(String(module).split('.')[0])));
  if (code.includes('matplotlib')) visit('matplotlib');
  for (const name of order) {
    if (!pyodide.loadedPackages[name]) await pyodide.loadPackage(name, { messageCallback: () => {} });
  }
}

async function main() {
  const input = JSON.parse(fs.readFileSync(0, 'utf8') || '{}');
  const code = String(input.code || '');
  let stdout = '';
  let stderr = '';
  let result = '';
  const keep = (buffer, line) => (buffer.length < MAX_OUTPUT ? `${buffer}${line}\n` : buffer);
  try {
    const { loadPyodide } = require(path.join(DIR, 'pyodide.js'));
    const pyodide = await loadPyodide({
      indexURL: DIR,
      // `import js` sees only these: Python's asyncio needs the timers.
      jsglobals: { setTimeout, clearTimeout, setInterval, clearInterval },
      stdout: (line) => { stdout = keep(stdout, line); },
      stderr: (line) => { stderr = keep(stderr, line); },
    });
    // input() cannot wait for anyone here: it raises instead of hanging.
    pyodide.setStdin({ error: true });
    await loadImports(pyodide, code);
    await pyodide.runPythonAsync(PANDAS_QUIET);
    if (code.includes('matplotlib')) await pyodide.runPythonAsync(MATPLOTLIB_SHOW);
    // The Pyodide API (file system mounts, Node access) is out of reach from here on.
    pyodide.runPython("import sys\nsys.modules.pop('pyodide_js', None)");
    pyodide.unregisterJsModule('pyodide_js');
    result = text(await pyodide.runPythonAsync(code));
  } catch (error) {
    stderr = keep(stderr, String(error && error.message ? error.message : error));
  }
  process.stdout.write(`${JSON.stringify({ stdout, stderr, result })}\n`);
}

main().then(() => process.exit(0), (error) => {
  process.stdout.write(`${JSON.stringify({ stdout: '', stderr: String(error), result: '' })}\n`);
  process.exit(0);
});
