// turns.js — a conversation as turns (a question and its answer), with the
// versions of a question (edit, regenerate: messages.version_of, backend
// research_runs.begin_run). Only the last turn can have versions; a replaced
// version keeps its answer (superseded_at) for the audit and the switcher.

/**
 * @returns {Array<{root: string, versions: Array<{question, answer}>, current: number}>}
 * root: the first question of the group; current: the index of the version
 * not replaced (the last one when all are).
 */
export function buildTurns(messages = []) {
  const turns = [];
  const byRoot = new Map();
  let last = null;
  for (const m of messages) {
    if (m.sender === 'user') {
      const root = m.version_of || m.id;
      const version = { question: m, answer: null };
      if (byRoot.has(root)) byRoot.get(root).versions.push(version);
      else {
        const turn = { root, versions: [version] };
        byRoot.set(root, turn);
        turns.push(turn);
      }
      last = version;
    } else if (last && !last.answer) {
      last.answer = m;
    } else {
      // An answer without its question (older data): its own turn.
      turns.push({ root: m.id, versions: [{ question: null, answer: m }] });
      last = null;
    }
  }
  for (const turn of turns) {
    const live = turn.versions.findIndex((v) => !v.question?.superseded_at);
    turn.current = live === -1 ? turn.versions.length - 1 : live;
  }
  return turns;
}

/** Counts for the process line of a finished answer. */
export function processSummary(answer) {
  const steps = answer?.steps || [];
  // Pages read (live steps), else the pages cited (a reloaded answer).
  const pages = new Set();
  for (const s of steps) if ((s.tool === 'read' || s.tool === 'open') && s.file) pages.add(`${s.file}:${s.page}`);
  if (!pages.size) for (const c of answer?.citations || []) pages.add(`${c.document_id}:${c.pdf_page_no ?? c.page_no}`);
  const searches = steps.filter((s) => s.tool === 'search' || s.tool === 'grep').length;
  const web = steps.filter((s) => s.tool === 'search_web' || s.tool === 'fetch_url').length
    || (answer?.web_sources?.length ? 1 : 0);
  const code = steps.filter((s) => s.tool === 'execute_code').length || answer?.code_runs?.length || 0;
  const searched = searches > 0 || answer?.course_use === 'cited' || answer?.course_use === 'not_found';
  const seconds = answer?.started_at && answer?.finished_at
    ? Math.max(1, Math.round((answer.finished_at - answer.started_at) / 1000)) : null;
  return { searched, pages: pages.size, web, code, seconds, courseUse: answer?.course_use ?? null };
}
