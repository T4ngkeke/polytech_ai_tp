// Ported from Open WebUI v0.13.4, src/lib/workers/pyodide.worker.ts (loading
// Pyodide in a worker, stdout/stderr capture, packages from imports, the
// matplotlib show() that prints a base64 PNG line).
// Copyright (c) 2023- Open WebUI Inc. [Created by Timothy Jaeryang Baek]
// All rights reserved. Used under the Open WebUI License, with its conditions
// and disclaimer: see third_party/open-webui/LICENSE.
//
// Python in the student's browser (backend/app/agent/code_tools.py). Messages:
// in {id, code}; out {id, phase: 'ready'} once Python and the imported packages
// are loaded (the page starts the run clock then), and {id, stdout, stderr,
// result} at the end. The worker keeps its Python state between runs of one
// page; the page ends it when loading or a run takes too long.
/* global importScripts, loadPyodide */

// Served by our nginx (frontend/scripts/fetch-pyodide.mjs), not a CDN.
const PYODIDE_URL = `${self.location.origin}/pyodide/`;
let ready = null;

function load() {
  if (!ready) {
    ready = (async () => {
      importScripts(`${PYODIDE_URL}pyodide.js`);
      const pyodide = await loadPyodide({ indexURL: PYODIDE_URL });
      // pandas 2.2 warns about pyarrow on every import: noise for students.
      await pyodide.runPythonAsync(PANDAS_QUIET);
      return pyodide;
    })();
    // A failed load (network) is tried again on the next run.
    ready.catch(() => { ready = null; });
  }
  return ready;
}

const PANDAS_QUIET = `
import warnings
warnings.filterwarnings("ignore", message=r"(?s)\\s*Pyarrow will become a required dependency")
`;

const MATPLOTLIB_SHOW = `
import base64
import os
from io import BytesIO
os.environ["MPLBACKEND"] = "AGG"
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

self.onmessage = async (event) => {
  const { id, code } = event.data;
  let stdout = '';
  let stderr = '';
  let result = '';
  try {
    const pyodide = await load();
    pyodide.setStdout({ batched: (line) => { stdout += `${line}\n`; } });
    pyodide.setStderr({ batched: (line) => { stderr += `${line}\n`; } });
    // input() cannot wait for the student here: it raises instead of hanging.
    pyodide.setStdin({ error: true });
    await pyodide.loadPackagesFromImports(code);
    if (code.includes('matplotlib')) await pyodide.runPythonAsync(MATPLOTLIB_SHOW);
    self.postMessage({ id, phase: 'ready' });
    result = text(await pyodide.runPythonAsync(code));
  } catch (error) {
    stderr += String(error && error.message ? error.message : error);
  }
  self.postMessage({ id, stdout, stderr, result });
};
