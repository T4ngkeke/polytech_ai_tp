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
// first question and answer).

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
      // mode: the answer mode the server applies (exam mode may turn it into direct).
      const parsed = safeParse(ev.data);
      return { type: 'status', route: parsed?.route ?? null, phase: parsed?.phase ?? null,
        mode: parsed?.mode ?? null, examMode: parsed?.exam_mode === true };
    }
    case 'done':
      return { type: 'done', messageId: safeParse(ev.data)?.message_id ?? null };
    case 'error':
      return { type: 'error', detail: safeParse(ev.data)?.detail || 'Generation failed' };
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
