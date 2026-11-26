// streamEvents.js — shared interpreter for the chat SSE stream.
//
// The server (backend/app/routers/chat.py) opens the stream with a named
// `status` event, then streams JSON-encoded content tokens as anonymous
// `data:` frames, and closes with `citations` + `done` (or `error`). Both the
// student chat (Chat.jsx) and the teacher test-drive (Teacher.jsx) consume this,
// so the event→action decision lives here — matching named events FIRST so a
// status/done/error/citations frame is never mistaken for a content token (which
// would append "[object Object]" to the reply).
//
// The research agent also sends `draft` (reply text as the model writes it,
// before its sources are checked) and `draft_reset` (that draft is withdrawn).
// The checked answer arrives as a content-token frame at the end. When the
// answer is a question back, `clarification` follows with its options.
// After `done`, a new conversation may receive its `title` (written from the
// first question and answer), then `follow_ups` (suggested next questions).
// While the agent works: `step` (what one tool call did), `tasks` (its
// checklist), `code` (Python for this browser to run, agent/tools/code.py).

import { quotaRefusal } from './quota.js';

function safeParse(data) {
  try {
    return JSON.parse(data);
  } catch {
    return undefined;
  }
}

/**
 * Classify one SSE event into an action the UI can apply.
 * @param {{event?: string, data?: string}} ev
 * @returns {{type: 'status'|'done'|'error'|'citations'|'clarification'|'draft'|'draft_reset'|'title'|'token'|'ignore', [k: string]: any}}
 */
export function interpretStreamEvent(ev) {
  switch (ev.event) {
    case 'status': {
      // examMode: whether the class is in exam mode now (the page reloads its classes when it changed).
      const parsed = safeParse(ev.data);
      return { type: 'status', route: parsed?.route ?? null, phase: parsed?.phase ?? null,
        examMode: parsed?.exam_mode === true };
    }
    case 'done': {
      const parsed = safeParse(ev.data) || {};
      return { type: 'done', messageId: parsed.message_id ?? null,
        userMessageId: parsed.user_message_id ?? null, courseUse: parsed.course_use ?? null,
        webSources: Array.isArray(parsed.web_sources) ? parsed.web_sources : null,
        codeRuns: Array.isArray(parsed.code_runs) ? parsed.code_runs : null };
    }
    case 'step': {
      const parsed = safeParse(ev.data);
      return parsed && typeof parsed.tool === 'string' ? { type: 'step', step: parsed } : { type: 'ignore' };
    }
    case 'tasks': {
      const parsed = safeParse(ev.data);
      return { type: 'tasks', tasks: Array.isArray(parsed) ? parsed : [] };
    }
    case 'code': {
      // server: the server runs it (python_runner); else this page runs it.
      const parsed = safeParse(ev.data);
      return parsed && typeof parsed.id === 'string' && typeof parsed.code === 'string'
        ? { type: 'code', id: parsed.id, code: parsed.code, server: parsed.where === 'server' } : { type: 'ignore' };
    }
    case 'code_output': {
      // What a server run returned: {stdout, stderr, result}, or {error}.
      const parsed = safeParse(ev.data);
      if (!parsed || typeof parsed.id !== 'string' || !parsed.output) return { type: 'ignore' };
      const { stdout = '', stderr = '', result = '', error = null } = parsed.output;
      const failed = Boolean(error) || /Traceback|Error:/.test(stderr || '');
      return { type: 'code_output', id: parsed.id,
        output: { status: failed ? 'error' : 'ok', stdout, stderr: stderr || error || '', result } };
    }
    case 'question': {
      const parsed = safeParse(ev.data);
      return parsed?.user_message_id ? { type: 'question', userMessageId: parsed.user_message_id } : { type: 'ignore' };
    }
    case 'follow_ups': {
      const parsed = safeParse(ev.data);
      const list = Array.isArray(parsed?.follow_ups) ? parsed.follow_ups.filter((x) => typeof x === 'string') : [];
      return { type: 'follow_ups', messageId: parsed?.message_id ?? null, followUps: list };
    }
    case 'error':
      return { type: 'error', detail: safeParse(ev.data)?.detail || 'Generation failed', code: safeParse(ev.data)?.code || null };
    case 'draft': {
      const parsed = safeParse(ev.data);
      return { type: 'draft', text: typeof parsed === 'string' ? parsed : '' };
    }
    case 'draft_reset':
      return { type: 'draft_reset' };
    case 'title': {
      const parsed = safeParse(ev.data);
      return typeof parsed?.title === 'string' && parsed.title
        ? { type: 'title', sessionId: parsed.session_id ?? null, title: parsed.title }
        : { type: 'ignore' };
    }
    case 'clarification': {
      const parsed = safeParse(ev.data);
      const options = Array.isArray(parsed?.options) ? parsed.options.filter((o) => typeof o === 'string') : [];
      return { type: 'clarification', clarification: { question: parsed?.question ?? '', options } };
    }
    case 'citations': {
      const parsed = safeParse(ev.data);
      return { type: 'citations', citations: Array.isArray(parsed) ? parsed : [] };
    }
    default:
      // No event name → a content-token frame. Tokens are JSON-encoded strings so
      // embedded newlines survive SSE framing; fall back to the raw string.
      if (ev.data) {
        const parsed = safeParse(ev.data);
        return { type: 'token', text: typeof parsed === 'string' ? parsed : ev.data };
      }
      return { type: 'ignore' };
  }
}

/**
 * What to tell the student when the server refuses a question before the
 * stream starts (routers/chat.py: lab locked, not in the class, quota used,
 * conversation gone, changed in another window). `reload` names what the page
 * should load again: 'classes' (the lab or class changed), 'usage' (the quota
 * bar) or 'conversation'. `messageKey`: the same message in the i18n strings.
 * @param {number} status HTTP status
 * @param {string|null} detail the server's `detail`, when it is a string
 */
export function streamRefusal(status, detail) {
  // The conversation changed in another window (a newer question, or this one replaced).
  if (detail === 'not_latest_question' || detail === 'message_not_found') {
    return { message: 'This conversation changed in another window; it was loaded again.',
      messageKey: 'chat.changedElsewhere', reload: 'conversation' };
  }
  if (status === 429) return { message: quotaRefusal(detail), reload: 'usage' };
  if (status === 403) return { message: detail || 'You can no longer ask questions in this lab.', reload: 'classes' };
  if (status === 404) return { message: 'This conversation no longer exists. Open the lab again.', reload: 'classes' };
  if (status >= 500) return { message: `The server could not take the question (error ${status}). Try again in a moment.`, reload: null };
  return { message: detail || `The question was refused (error ${status}).`, reload: null };
}

/**
 * fetchEventSource `onopen`: let an event stream through; otherwise throw an
 * error carrying `refusal` (streamRefusal) so the caller can explain it.
 */
export async function openStream(res) {
  if (res.ok && (res.headers.get('content-type') || '').includes('text/event-stream')) return;
  let detail = null;
  try {
    const body = await res.json();
    if (typeof body?.detail === 'string') detail = body.detail;
  } catch { /* not JSON */ }
  const error = new Error('stream_refused');
  error.refusal = streamRefusal(res.status, detail);
  throw error;
}
