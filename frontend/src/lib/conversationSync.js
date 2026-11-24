// conversationSync.js — whether a page showing a conversation must load it
// again, from the server's state (routers/chat.py GET /sessions/{id}/state);
// used by pages/student/useConversationSync.js.

const isLocalId = (id) => typeof id === 'string' && id.startsWith('local-');

/** The saved, current questions on the page (a version replaced is not one). */
export function shownQuestions(messages = []) {
  return messages.filter((m) => m.sender === 'user' && !m.superseded_at && !isLocalId(m.id));
}

/** Whether the page must load the conversation again, from the server's state. */
export function needsReload(state, messages, wasRunning) {
  const shown = shownQuestions(messages);
  return (wasRunning && !state.running)
    || state.questions !== shown.length
    || (state.last_question_id || null) !== (shown[shown.length - 1]?.id || null);
}

