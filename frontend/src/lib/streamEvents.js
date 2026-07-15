// streamEvents.js — [v8.0] shared interpreter for the chat SSE stream.
//
// The server (backend/app/routers/chat.py) opens the stream with a named
// `status` event, then streams JSON-encoded content tokens as anonymous
// `data:` frames, and closes with `citations` + `done` (or `error`). Both the
// student chat (Chat.jsx) and the teacher test-drive (Teacher.jsx) consume this,
// so the event→action decision lives here — matching named events FIRST so a
// status/done/error/citations frame is never mistaken for a content token (which
// would append "[object Object]" to the reply).

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
 * @returns {{type: 'status'|'done'|'error'|'citations'|'token'|'ignore', [k: string]: any}}
 */
export function interpretStreamEvent(ev) {
  switch (ev.event) {
    case 'status':
      return { type: 'status', route: safeParse(ev.data)?.route ?? null };
    case 'done':
      return { type: 'done' };
    case 'error':
      return { type: 'error', detail: safeParse(ev.data)?.detail || 'Generation failed' };
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
