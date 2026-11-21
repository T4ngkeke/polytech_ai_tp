// chart.js — the ```chart JSON of an answer, read again on the page with the
// same rules as the server (backend agent/visuals.py Chart), and axis ticks.

const TYPES = new Set(['bar', 'line', 'scatter', 'pie']);
const KEYS = new Set(['type', 'title', 'x_label', 'y_label', 'labels', 'series']);
const MAX_POINTS = 200;

const isNumber = (v) => typeof v === 'number' && Number.isFinite(v);

/** The chart, or null when the JSON does not have the checked shape. */
export function parseChart(source) {
  let c;
  try { c = JSON.parse(source); } catch { return null; }
  if (!c || typeof c !== 'object' || Array.isArray(c) || !TYPES.has(c.type)) return null;
  if (Object.keys(c).some((k) => !KEYS.has(k))) return null;
  const { labels, series } = c;
  if (!Array.isArray(labels) || !labels.length || labels.length > MAX_POINTS) return null;
  if (!labels.every((l) => typeof l === 'string' || isNumber(l))) return null;
  if (!Array.isArray(series) || !series.length || series.length > 8) return null;
  for (const s of series) {
    if (!s || !Array.isArray(s.values) || s.values.length !== labels.length) return null;
    if (!s.values.every((v) => v === null || isNumber(v))) return null;
  }
  if (c.type === 'scatter' && !labels.every(isNumber)) return null;
  if (c.type === 'pie' && (series.length !== 1 || !series[0].values.every((v) => isNumber(v) && v >= 0))) return null;
  const text = (v) => (typeof v === 'string' ? v.slice(0, 120) : '');
  return { type: c.type, title: text(c.title), x_label: text(c.x_label), y_label: text(c.y_label),
    labels, series: series.map((s) => ({ name: text(s.name), values: s.values })) };
}

/** [min, max, ticks] rounded to a nice step, about five ticks. */
export function niceTicks(min, max, count = 5) {
  if (!isNumber(min) || !isNumber(max)) return [0, 1, [0, 1]];
  if (min === max) { min -= 1; max += 1; }
  const raw = (max - min) / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) || raw;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Math.round(v / step) * step);
  return [lo, hi, ticks];
}
