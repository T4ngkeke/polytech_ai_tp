/**
 * What a document being read shows under its name (components/DocumentManager.jsx):
 * the jobs ahead, the pages read and the time left, then indexing.
 * The server sends latest_revision.jobs_ahead (queued) or .progress (reading).
 * Also the status a document shows, in the teacher list and the admin
 * Documents tab (pages/Admin.jsx).
 */

// A structured document has no published version until it is indexed.
// Until then its newest revision tells what is going on.
const REVISION_STATUS = { queued: 'pending', processing: 'processing', parsed: 'parsed', partial: 'needs_review', failed: 'failed' };

/** The status to show: pending, processing, parsed, indexed, needs_review, failed… */
export function displayStatus(doc) {
  if (doc.structured && !doc.current_revision_id && doc.latest_revision) {
    const state = doc.latest_revision.processing_state;
    return REVISION_STATUS[state] || state;
  }
  return doc.status;
}

const WORKING = new Set(['pending', 'processing', 'parsed']);
const PROBLEM = new Set(['failed', 'needs_review', 'partially_indexed']);

/** 'working' (still being read), 'problem' (failed or pages missing) or 'ready'. */
export function statusGroup(status) {
  if (WORKING.has(status)) return 'working';
  if (PROBLEM.has(status)) return 'problem';
  return 'ready';
}

/** "about 8 min left", "less than a minute left"; null when not known yet. */
export function timeLeft(seconds) {
  if (seconds == null) return null;
  if (seconds < 60) return 'less than a minute left';
  return `about ${Math.round(seconds / 60)} min left`;
}

/**
 * { text, share } for a document still on its way, share from 0 to 1 for the
 * bar; null when the document is not being processed. `status` is the shown
 * status (pending, processing, parsed).
 */
export function readingLine(status, latest) {
  if (status === 'pending') {
    const ahead = latest?.jobs_ahead ?? 0;
    return { text: ahead ? `Waiting: ${ahead} document${ahead === 1 ? '' : 's'} ahead` : 'Waiting to start', share: 0 };
  }
  if (status === 'processing') {
    const p = latest?.progress;
    if (!p?.total) return { text: 'Opening the PDF…', share: 0 };
    const left = timeLeft(p.seconds_left);
    return {
      text: `${p.done} of ${p.total} pages read${left ? ` · ${left}` : ''}`,
      share: 0.95 * Math.min(1, p.done / p.total),   // indexing is the last 5%
    };
  }
  if (status === 'parsed') return { text: 'Indexing…', share: 0.97 };
  return null;
}
