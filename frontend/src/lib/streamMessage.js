// streamMessage.js — how one stream action (lib/streamEvents.js) changes the
// assistant message being answered. Shared by the student chat and the
// teacher's test drive, so both show the same draft, answer and options.

/** The message after `action`; other action types leave it unchanged. */
export function applyToMessage(message, action) {
  switch (action.type) {
    // The reply as the model writes it, before its sources are checked.
    case 'draft':
      return { ...message, draft: (message.draft || '') + action.text };
    case 'draft_reset':
      return { ...message, draft: '' };
    // The checked answer replaces the draft.
    case 'token':
      return { ...message, content: message.content + action.text, draft: '' };
    case 'citations':
      return { ...message, citations: action.citations };
    // A question back to the student, with options to pick.
    case 'clarification':
      return { ...message, clarification: action.clarification };
    default:
      return message;
  }
}
