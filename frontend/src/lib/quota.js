/**
 * quota.js — the student's day and week token limits, as text.
 *
 * Data: GET /api/student/usage → { used, limit, week_used, week_limit,
 * blocked: 'day'|'week'|null, resets_at, week_resets_at, reset_requested }.
 * Days start at midnight Paris time, weeks on Monday (backend quota_service).
 * A question that started always finishes; the next one is refused.
 */

/** Share used, 0 to 100. */
export function percent(used, limit) {
  if (!limit) return 0;
  return Math.min(100, Math.round((100 * used) / limit));
}

/** "in 6 h", "in 25 min", "in 3 days". */
export function resetIn(iso, now = new Date()) {
  const ms = new Date(iso).getTime() - now.getTime();
  if (!(ms > 0)) return 'soon';
  const minutes = Math.ceil(ms / 60000);
  if (minutes < 60) return `in ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `in ${hours} h`;
  return `in ${Math.round(hours / 24)} days`;
}

/** The sentence shown while a new question is refused, or null. */
export function blockedMessage(usage, now = new Date()) {
  if (usage?.blocked === 'day') {
    return `You have reached today's limit. It resets at midnight (${resetIn(usage.resets_at, now)}).`;
  }
  if (usage?.blocked === 'week') {
    return `You have reached this week's limit. It resets on Monday (${resetIn(usage.week_resets_at || usage.resets_at, now)}).`;
  }
  return null;
}

/**
 * The limit the student should be told about once (80% used), or null.
 * Returns { kind: 'day'|'week', key } — key is remembered so the notice is shown
 * once per day (or week).
 */
export function nearLimit(usage) {
  if (!usage || usage.blocked) return null;
  if (percent(usage.used, usage.limit) >= 80) {
    return { kind: 'day', key: `quota80:day:${usage.resets_at}` };
  }
  if (percent(usage.week_used, usage.week_limit) >= 80) {
    return { kind: 'week', key: `quota80:week:${usage.week_resets_at}` };
  }
  return null;
}

export function nearLimitMessage(kind) {
  return kind === 'week'
    ? "You have used 80% of this week's limit. It resets on Monday."
    : "You have used 80% of today's limit. It resets at midnight.";
}

/** The refusal sent by the chat (429 detail) as a sentence. */
export function quotaRefusal(detail) {
  return detail === 'week_quota_reached'
    ? "You have reached this week's limit."
    : "You have reached today's limit.";
}
