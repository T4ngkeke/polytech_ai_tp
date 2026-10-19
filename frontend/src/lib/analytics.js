/**
 * Pure helpers of the teacher Analytics tab (components/TeacherAnalytics.jsx).
 * The data comes from GET /api/teacher/analytics/classes/{id}/details.
 */

export const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/** "1 question", "3 questions". */
export function questionCount(n) {
  return `${n} question${n === 1 ? '' : 's'}`;
}

/**
 * Horizontal shift of a chart tooltip centred on x in a chart `width` wide,
 * so a tooltip near an edge stays inside the chart.
 */
export function tipShift(x, width) {
  if (x < width * 0.15) return '0%';
  if (x > width * 0.85) return '-100%';
  return '-50%';
}

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

// ── Admin Classes tab (pages/Admin.jsx) ─────────────────────────────────────

/** YYYY-MM-DD of a date on the viewer's calendar. */
function isoDay(d) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/**
 * The last `n` days, oldest first, as {date, questions}: the platform usage
 * rows (GET /api/admin/analytics daily_breakdown) only list days with use.
 */
export function lastDays(rows, n = 30, today = new Date()) {
  const byDay = Object.fromEntries(rows.map((r) => [r.date, r.requests]));
  return Array.from({ length: n }, (_, i) => {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - (n - 1 - i));
    const date = isoDay(d);
    return { date, questions: byDay[date] || 0 };
  });
}

/** Questions of the platform in the last 7 days of `lastDays`. */
export function lastWeek(days) {
  return days.slice(-7).reduce((sum, d) => sum + d.questions, 0);
}

/** Class names per teacher id, for the Users tab. */
export function classesByTeacher(classes) {
  const out = {};
  for (const c of classes) (out[c.teacher_id] ||= []).push(c.name);
  return out;
}

const CLASS_SORTS = {
  name: (c) => c.name.toLowerCase(),
  teacher: (c) => (c.teacher_username || '').toLowerCase(),
  students: (c) => c.student_count,
  questions_7d: (c) => c.questions_7d,
  last_active: (c) => (c.last_active ? new Date(c.last_active).getTime() : 0),
};

/** Classes sorted on one column; ties by class name. */
export function sortClasses(classes, key, dir = 'asc') {
  const value = CLASS_SORTS[key] || CLASS_SORTS.name;
  const sign = dir === 'asc' ? 1 : -1;
  return [...classes].sort((a, b) => {
    const x = value(a);
    const y = value(b);
    if (x < y) return -sign;
    if (x > y) return sign;
    return a.name.localeCompare(b.name);
  });
}
