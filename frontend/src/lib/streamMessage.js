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
    // What the agent did (the process line) and its checklist.
    case 'status':
      return action.phase && action.phase !== (message.phases || []).at(-1)
        ? { ...message, phases: [...(message.phases || []), action.phase] } : message;
    case 'step':
      return { ...message, steps: [...(message.steps || []), action.step] };
    case 'tasks':
      return { ...message, tasks: action.tasks };
    // A Python run the page is doing: {id, code, status: loading|running|ok|error, ...output}.
    case 'code':
      return { ...message, live_runs: [...(message.live_runs || []),
        { id: action.id, code: action.code, status: action.server ? 'running' : 'loading',
          where: action.server ? 'server' : 'browser' }] };
    case 'code_phase':
      return { ...message, live_runs: (message.live_runs || []).map((r) => (r.id === action.id && r.status === 'loading'
        ? { ...r, status: action.phase } : r)) };
    case 'code_output':
      return { ...message, live_runs: (message.live_runs || []).map((r) => (r.id === action.id ? { ...r, ...action.output } : r)) };
    case 'done':
      return { ...message, course_use: action.courseUse, web_sources: action.webSources,
        code_runs: action.codeRuns, finished_at: action.at ?? Date.now() };
    case 'follow_ups':
      return { ...message, follow_ups: action.followUps };
    default:
      return message;
  }
}
