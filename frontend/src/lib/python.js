/**
 * python.js — run Python in this browser (public/pyodide-worker.js, Pyodide
 * served from /pyodide/<PYODIDE_VERSION>/ by our nginx).
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
 *
 * In Safari (and any iOS browser: all are WebKit) matplotlib's own drawing
 * (Agg) gives blank figures at times (seen in Safari 27.2 with Pyodide 0.26 to
 * 0.29). There the figure comes out as SVG and this page draws it into a PNG,
 * so the rest (display, chat file, the model) sees a PNG as everywhere else.
 */
import api from './api';

// Same as VERSION in scripts/fetch-pyodide.mjs (pyodideVersion.test.js checks it).
export const PYODIDE_VERSION = '0.29.3';
// A first load is ~20 MB (Python, numpy, matplotlib): over a slow link (~250 kB/s
// seen through a forwarded port) that is 80 s. Later loads come from the cache.
export const LOAD_SECONDS = 180;
export const RUN_SECONDS = 50;
const AGENT = typeof navigator === 'undefined' ? '' : navigator.userAgent;
export const DRAW_SVG = /AppleWebKit/.test(AGENT) && !/Chrome\/|Chromium\//.test(AGENT);
const SVG_LINE = 'data:image/svg+xml;base64,';
let worker = null;
let counter = 0;
const waiting = new Map();

function getWorker() {
  if (!worker) {
    worker = new Worker(`/pyodide-worker.js?pyodide=${PYODIDE_VERSION}`);
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

/** A PNG data URL of an SVG data URL from matplotlib, at Agg's 100 dpi. */
async function pngOfSvg(src) {
  const svg = new TextDecoder().decode(Uint8Array.from(atob(src.slice(SVG_LINE.length)), (c) => c.charCodeAt(0)));
  const points = (name) => Number(svg.match(new RegExp(`<svg[^>]*\\s${name}="([\\d.]+)pt"`))?.[1]) || null;
  const image = new Image();
  await new Promise((ok, fail) => { image.onload = ok; image.onerror = fail; image.src = src; });
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(points('width') ? points('width') * 100 / 72 : image.naturalWidth);
  canvas.height = Math.round(points('height') ? points('height') * 100 / 72 : image.naturalHeight);
  const context = canvas.getContext('2d');
  context.fillStyle = '#fff';
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/png');
}

/** The output with each SVG figure line turned into a PNG line. */
async function withPngFigures(output) {
  if (!output.stdout?.includes(SVG_LINE)) return output;
  const lines = [];
  let failed = false;
  for (const line of output.stdout.split('\n')) {
    const at = line.indexOf(SVG_LINE);
    if (at === -1) { lines.push(line); continue; }
    try {
      lines.push(line.slice(0, at) + await pngOfSvg(line.slice(at).trim()));
    } catch {
      failed = true;
    }
  }
  const stderr = failed ? `${output.stderr || ''}A figure could not be drawn in this browser.\n` : output.stderr;
  return { ...output, stdout: lines.join('\n'), stderr };
}

/** {stdout, stderr, result, error?} of one run; onPhase('running') once Python is loaded. */
export function runPython(code, { onPhase = null, loadSeconds = LOAD_SECONDS, runSeconds = RUN_SECONDS, svg = DRAW_SVG } = {}) {
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
    const done = (output) => { clearTimeout(timer); withPngFigures(output).then(resolve); };
    done.phase = () => {
      clearTimeout(timer);
      timer = setTimeout(stop(runSeconds, 'timeout'), runSeconds * 1000);
      onPhase?.('running');
    };
    timer = setTimeout(stop(loadSeconds, 'Python did not load'), loadSeconds * 1000);
    waiting.set(id, done);
    try {
      getWorker().postMessage({ id, code, svg });
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
