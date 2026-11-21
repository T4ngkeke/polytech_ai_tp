/**
 * python.js — run Python in this browser (public/pyodide-worker.js, Pyodide).
 *
 * The research agent asks for a run with a `code` stream event; the page runs
 * it here and posts the output to /api/chat/code/{id} (backend
 * agent/code_tools.py waits 60 s for it). A Run button on a Python code block
 * of an answer uses the same worker, without the server.
 *
 * One worker per page keeps the Python state between runs. A run that takes
 * longer than RUN_SECONDS ends the worker (the next run starts a new one).
 */
import api from './api';

export const RUN_SECONDS = 50;
let worker = null;
let counter = 0;
const waiting = new Map();

function getWorker() {
  if (!worker) {
    worker = new Worker('/pyodide-worker.js');
    worker.onmessage = (event) => {
      const done = waiting.get(event.data.id);
      if (done) {
        waiting.delete(event.data.id);
        done(event.data);
      }
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

/** {stdout, stderr, result, error?} of one run. */
export function runPython(code, seconds = RUN_SECONDS) {
  return new Promise((resolve) => {
    const id = `run-${++counter}`;
    const timer = setTimeout(() => {
      if (!waiting.has(id)) return;
      waiting.delete(id);
      worker?.terminate();
      worker = null;
      resolve({ stdout: '', stderr: '', result: '', error: `timeout after ${seconds} s` });
    }, seconds * 1000);
    waiting.set(id, (output) => { clearTimeout(timer); resolve(output); });
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
export async function runForAgent(callId, code) {
  const output = await runPython(code);
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
