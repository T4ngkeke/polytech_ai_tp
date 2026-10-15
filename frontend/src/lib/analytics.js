/**
 * Pure helpers of the teacher Analytics tab (components/TeacherAnalytics.jsx).
 * The data comes from GET /api/teacher/analytics/classes/{id}/details.
 */

export const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/** Shade 0 (none) to 4 (busiest hour) of one hour-of-week cell. */
export function heatLevel(count, max) {
  if (!count || !max) return 0;
  return Math.max(1, Math.ceil((4 * count) / max));
}

/** The busiest weekday and hour, or null when nobody asked anything. */
export function busiestHour(heatmap) {
  let best = null;
  heatmap.forEach((hours, day) => hours.forEach((count, hour) => {
    if (count && (!best || count > best.count)) best = { day: WEEKDAYS[day], hour, count };
  }));
  return best;
}

/** "3 days ago", "today", "never" — days counted on the viewer's calendar. */
export function lastActive(iso, now = new Date()) {
  if (!iso) return 'never';
  const then = new Date(iso);
  const days = Math.round((startOfDay(now) - startOfDay(then)) / 86400000);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  return `${days} days ago`;
}

function startOfDay(d) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/** Share of today's limit used, 0 to 1. */
export function quotaShare(used, limit) {
  if (!limit) return 0;
  return Math.min(1, used / limit);
}

/** Refill gives back a full quota; it does nothing while a full quota is left. */
export function canRefill(student) {
  return student.member && student.used_today > student.granted_today;
}

const SORTS = {
  username: (s) => s.username.toLowerCase(),
  questions: (s) => s.questions,
  last_active: (s) => (s.last_active ? new Date(s.last_active).getTime() : 0),
  today: (s) => quotaShare(s.used_today, s.limit_today),
  thumbs_down: (s) => s.thumbs_down,
};

export function sortStudents(students, key, dir = 'desc') {
  const value = SORTS[key] || SORTS.username;
  const sign = dir === 'asc' ? 1 : -1;
  return [...students].sort((a, b) => {
    const x = value(a);
    const y = value(b);
    if (x < y) return -sign;
    if (x > y) return sign;
    return a.username.localeCompare(b.username);
  });
}

/** "13 Oct" for a YYYY-MM-DD day, without a time zone shift. */
export function shortDay(day) {
  const [y, m, d] = day.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
}
