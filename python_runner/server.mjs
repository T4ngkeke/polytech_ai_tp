// python_runner/server.mjs — runs the research agent's Python on the server
// (backend/app/agent/tools/code.py, PYTHON_RUNNER_URL). One fresh process per
// run (run.cjs): nothing is kept from one run to the next. Runs wait in a queue,
// MAX_PARALLEL at a time; a run is killed after RUN_SECONDS.
//
//   POST /run {code}  ->  {status: ok|error|timeout, stdout, stderr, result}
//   GET /health       ->  {ok: true}
import http from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const PORT = Number(process.env.PORT || 8000);
const MAX_PARALLEL = Number(process.env.MAX_PARALLEL || 2);
const RUN_SECONDS = Number(process.env.RUN_SECONDS || 60);
const MAX_CODE_CHARS = 20_000;
const MAX_REPLY_BYTES = 12 * 1024 * 1024;
const RUN = fileURLToPath(new URL('./run.cjs', import.meta.url));

let running = 0;
const waiting = [];

function next() {
  while (running < MAX_PARALLEL && waiting.length) {
    const job = waiting.shift();
    running += 1;
    execute(job.code).then(job.resolve).finally(() => { running -= 1; next(); });
  }
}

function enqueue(code) {
  return new Promise((resolve) => { waiting.push({ code, resolve }); next(); });
}

function execute(code) {
  return new Promise((resolve) => {
    // The Node permission model: Python can reach Node's own objects through
    // the JS proxies (a function's constructor), so the process may read only
    // Pyodide's files and run.cjs, and may not write files, start processes or
    // workers. No environment and a scratch folder: it sees nothing of the server.
    const dir = process.env.PYODIDE_DIR || '/pyodide/0.29.3/';
    const child = spawn(process.execPath, ['--experimental-permission', `--allow-fs-read=${dir}*`,
      `--allow-fs-read=${RUN}`, '--no-warnings', '--max-old-space-size=768', RUN], {
      cwd: '/tmp', env: { PYODIDE_DIR: dir },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let out = '';
    let err = '';
    let done = false;
    const finish = (reply) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve(reply);
    };
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      finish({ status: 'timeout', stdout: '', stderr: `The run took more than ${RUN_SECONDS} s and was stopped.`, result: '' });
    }, RUN_SECONDS * 1000);
    child.stdout.on('data', (chunk) => {
      if (out.length < MAX_REPLY_BYTES) out += chunk;
      else child.kill('SIGKILL');
    });
    child.stderr.on('data', (chunk) => { if (err.length < 8000) err += chunk; });
    child.on('close', () => {
      const last = out.trim().split('\n').pop() || '';
      try {
        const reply = JSON.parse(last);
        const failed = /Traceback|Error:/.test(reply.stderr || '');
        finish({ status: failed ? 'error' : 'ok', stdout: String(reply.stdout || ''),
          stderr: String(reply.stderr || ''), result: String(reply.result || '') });
      } catch {
        finish({ status: 'error', stdout: '', stderr: `The Python runner failed. ${err.slice(-500)}`.trim(), result: '' });
      }
    });
    child.stdin.end(JSON.stringify({ code }));
  });
}

function send(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(body));
}

http.createServer((req, res) => {
  if (req.method === 'GET' && req.url === '/health') return send(res, 200, { ok: true, running, waiting: waiting.length });
  if (req.method !== 'POST' || req.url !== '/run') return send(res, 404, { error: 'not_found' });
  let body = '';
  req.on('data', (chunk) => {
    body += chunk;
    if (body.length > MAX_CODE_CHARS * 2) req.destroy();
  });
  req.on('end', async () => {
    let code;
    try {
      code = JSON.parse(body).code;
    } catch {
      return send(res, 400, { error: 'invalid_json' });
    }
    if (typeof code !== 'string' || !code.trim() || code.length > MAX_CODE_CHARS) {
      return send(res, 400, { error: 'invalid_code' });
    }
    send(res, 200, await enqueue(code));
  });
}).listen(PORT, () => console.log(`python runner on ${PORT}, ${MAX_PARALLEL} at a time, ${RUN_SECONDS} s each`));
