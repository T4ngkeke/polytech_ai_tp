/**
 * The three answer modes a student can choose next to the chat input. The
 * server applies the class's exam mode itself (direct only), whatever is sent.
 * The choice is remembered per chat session, in this browser only.
 */

export const MODES = [
  // Times measured alone on the v9 stack (2026-10-15), one question at a time.
  { key: 'direct', label: 'Direct', hint: 'Answers at once, without the course files' },
  { key: 'search', label: 'Search', hint: 'One search in the course files, about 10 seconds' },
  { key: 'research', label: 'Research', hint: 'Deep search in the course files, about a minute' },
];
export const DEFAULT_MODE = 'research';

const key = (sessionId) => `edullm-mode-${sessionId}`;

const known = (mode) => MODES.some((m) => m.key === mode);

export function storedMode(sessionId, getStorage = () => localStorage) {
  if (!sessionId) return DEFAULT_MODE;
  try {
    const value = getStorage().getItem(key(sessionId));
    return known(value) ? value : DEFAULT_MODE;
  } catch {
    return DEFAULT_MODE;
  }
}

/**
 * The mode a session opens with: the one chosen for it in this browser, else
 * the mode of its last answer (another browser, cleared storage), else the default.
 */
export function sessionMode(sessionId, messages, getStorage = () => localStorage) {
  let value = null;
  try {
    value = sessionId ? getStorage().getItem(key(sessionId)) : null;
  } catch {
    // Blocked storage: the answers still tell.
  }
  if (known(value)) return value;
  const last = [...(messages || [])].reverse().find((m) => m.sender === 'llm' && known(m.answer_mode));
  return last ? last.answer_mode : DEFAULT_MODE;
}

export function saveMode(sessionId, mode, getStorage = () => localStorage) {
  if (!sessionId) return;
  try {
    getStorage().setItem(key(sessionId), mode);
  } catch {
    // Private window or blocked storage: the choice lasts until reload.
  }
}

/** The label under an answer, or null for answers written before the modes. */
export function modeLabel(mode) {
  if (mode === 'direct') return 'Direct · course files not consulted';
  if (mode === 'search') return 'Search';
  if (mode === 'research') return 'Research';
  return null;
}

/** A search answer that cites nothing: offer to research the same question. */
export function offersResearch(message) {
  return message?.sender === 'llm' && message.answer_mode === 'search' && !!message.content
    && !(message.citations?.length) && !message.clarification;
}
