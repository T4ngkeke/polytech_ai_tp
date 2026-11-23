/**
 * python.js — run Python in this browser (public/pyodide-worker.js, Pyodide
 * served from /pyodide/ by our nginx).
 *
 * The research agent asks for a run with a `code` stream event; the page runs
 * it here and posts the output to /api/chat/code/{id} (backend
 * agent/code_tools.py waits CODE_TIMEOUT for it, more than LOAD + RUN). A Run
 * button on a Python code block of an answer uses the same worker, without the server.
 *
 * One worker per page keeps the Python state between runs. Loading Python and
 * the imported packages may take up to LOAD_SECONDS (the first run of a page,
 * a slow device); the run itself then has RUN_SECONDS. Past either, the
 * worker ends (the next run starts a new one).
 */
import api from './api';

export const LOAD_SECONDS = 90;
export const RUN_SECONDS = 50;
let worker = null;
let counter = 0;
const waiting = new Map();

function getWorker() {
  if (!worker) {
    worker = new Worker('/pyodide-worker.js');
    worker.onmessage = (event) => {
      const done = waiting.get(event.data.id);
      if (!done) return;
      if (event.data.phase) {
        done.phase(event.data.phase);
        return;
      }
      waiting.delete(event.data.id);
      done(event.data);
    };
    worker.onerror = (event) => {
      const message = event?.message || 'Python could not start in this browser';
      waiting.forEach((done) => done({ stdout: '', stderr: message, result: '', error: message }));
      waiting.clear();
      worker?.terminate();
      worker = null;
    };
  }
  return worker;
}

/** {stdout, stderr, result, error?} of one run; onPhase('running') once Python is loaded. */
export function runPython(code, { onPhase = null, loadSeconds = LOAD_SECONDS, runSeconds = RUN_SECONDS } = {}) {
  return new Promise((resolve) => {
    const id = `run-${++counter}`;
    let timer = null;
    const stop = (seconds, what) => () => {
      if (!waiting.has(id)) return;
      waiting.delete(id);
      worker?.terminate();
      worker = null;
      resolve({ stdout: '', stderr: '', result: '', error: `${what} after ${seconds} s` });
    };
    const done = (output) => { clearTimeout(timer); resolve(output); };
    done.phase = () => {
      clearTimeout(timer);
      timer = setTimeout(stop(runSeconds, 'timeout'), runSeconds * 1000);
      onPhase?.('running');
    };
    timer = setTimeout(stop(loadSeconds, 'Python did not load'), loadSeconds * 1000);
    waiting.set(id, done);
    try {
      getWorker().postMessage({ id, code });
    } catch (err) {
      clearTimeout(timer);
      waiting.delete(id);
      resolve({ stdout: '', stderr: String(err), result: '', error: String(err) });
    }
  });
}

/** Run what the agent asked for and send the output back; returns the output. */
export async function runForAgent(callId, code, onPhase = null) {
  const output = await runPython(code, { onPhase });
  try {
    await api.post(`/api/chat/code/${encodeURIComponent(callId)}`, {
      stdout: output.stdout || '', stderr: output.stderr || '', result: output.result || '',
      error: output.error || null,
    });
  } catch { /* the run was no longer waiting (timeout, stopped) */ }
  return output;
}

/** Output lines with the images split out: [{kind: 'text', text} | {kind: 'image', src}]. */
export function outputParts(text) {
  const parts = [];
  let buffer = [];
  for (const line of String(text || '').split('\n')) {
    const at = line.indexOf('data:image/png;base64,');
    if (at === -1) { buffer.push(line); continue; }
    if (buffer.join('\n').trim()) parts.push({ kind: 'text', text: buffer.join('\n') });
    buffer = [];
    parts.push({ kind: 'image', src: line.slice(at).trim() });
  }
  if (buffer.join('\n').trim()) parts.push({ kind: 'text', text: buffer.join('\n').replace(/\n+$/, '') });
  return parts;
}
