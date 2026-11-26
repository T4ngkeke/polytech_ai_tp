/**
 * historyLine.js — the chat line above which the assistant no longer sees the
 * messages, and the length limit of a message.
 *
 * The server says where the next question's history starts
 * (GET /api/chat/sessions/{id}/history-start: seen_from, hidden; see
 * backend agent/context/history.py). Agreed on a prototype first (2026-11-18).
 */

// The longest message the server takes (schemas.MAX_MESSAGE_CHARS).
export const MAX_MESSAGE_CHARS = 20000;
// The character count shows from here on.
export const SHOW_COUNT_FROM = 16000;

/**
 * Where the line goes among the shown messages.
 * Returns { index, missing }: the line sits before messages[index]
 * (messages.length: after the last one; -1: no line). missing: the server
 * named a message this page only knows by a temporary id (sent just now), so
 * the messages must be loaded again.
 */
export function historyLineIndex(messages, start) {
  if (!start || !start.hidden) return { index: -1, missing: false };
  if (!start.seen_from) return { index: messages.length, missing: false };
  const index = messages.findIndex((m) => m.id === start.seen_from);
  return index < 0 ? { index: -1, missing: true } : { index, missing: false };
}

/** The state of the message box: the count to show ('' under SHOW_COUNT_FROM), and over the limit or not. */
export function messageLength(text) {
  const n = (text || '').length;
  return {
    over: n > MAX_MESSAGE_CHARS,
    near: n >= SHOW_COUNT_FROM,
    count: n >= SHOW_COUNT_FROM ? `${n.toLocaleString('en')} / ${MAX_MESSAGE_CHARS.toLocaleString('en')}` : '',
    excess: Math.max(0, n - MAX_MESSAGE_CHARS),
  };
}
