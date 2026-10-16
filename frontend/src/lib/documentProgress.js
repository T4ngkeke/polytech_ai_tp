/**
 * What a document being read shows under its name (components/DocumentManager.jsx):
 * the jobs ahead, the pages read and the time left, then indexing.
 * The server sends latest_revision.jobs_ahead (queued) or .progress (reading).
 */

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
