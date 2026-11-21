/**
 * Chart.jsx — a ```chart block of an answer: one JSON object in the shape the
 * server checks (backend agent/visuals.py Chart): {type: bar|line|scatter|pie,
 * title?, x_label?, y_label?, labels, series: [{name?, values}]}. Drawn as
 * plain SVG; nothing from the answer is run. Anything else shows as code.
 */
import { memo, useMemo } from 'react';
import { parseChart, niceTicks } from '../lib/chart.js';

const COLORS = ['rgb(var(--cream))', 'rgb(var(--cyan))', 'rgb(var(--gold))', 'rgb(var(--cream-muted))',
  'rgb(var(--success))', 'rgb(var(--danger))', 'rgb(var(--cyan-hover))', 'rgb(var(--cream-faint))'];
const W = 560;
const H = 280;
const PAD = { left: 52, right: 16, top: 16, bottom: 44 };

function Legend({ series }) {
  if (series.length < 2) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-cream-secondary">
      {series.map((s, i) => (
        <span key={i} className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: COLORS[i % COLORS.length] }} />
          {s.name || `#${i + 1}`}
        </span>
      ))}
    </div>
  );
}

function Pie({ chart }) {
  const values = chart.series[0].values.map((v) => v || 0);
  const total = values.reduce((a, b) => a + b, 0) || 1;
  let angle = -Math.PI / 2;
  const r = 100;
  const slices = values.map((v, i) => {
    const start = angle;
    angle += (v / total) * Math.PI * 2;
    const large = angle - start > Math.PI ? 1 : 0;
    const [x1, y1, x2, y2] = [Math.cos(start) * r, Math.sin(start) * r, Math.cos(angle) * r, Math.sin(angle) * r];
    const d = values.length === 1 ? `M ${-r} 0 A ${r} ${r} 0 1 1 ${r} 0 A ${r} ${r} 0 1 1 ${-r} 0`
      : `M 0 0 L ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} Z`;
    return <path key={i} d={d} fill={COLORS[i % COLORS.length]} stroke="rgb(var(--ink-raised))" strokeWidth="1.5" />;
  });
  return (
    <div className="flex flex-wrap items-center gap-6">
      <svg viewBox="-110 -110 220 220" className="h-52 w-52" role="img" aria-label={chart.title || 'chart'}>{slices}</svg>
      <div className="space-y-1 text-[13px] text-cream-secondary">
        {chart.labels.map((label, i) => (
          <div key={i} className="flex items-center gap-2">
            <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: COLORS[i % COLORS.length] }} />
            <span>{label}</span>
            <span className="tabular-nums text-cream-muted">{Math.round((values[i] / total) * 1000) / 10}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Axes({ chart }) {
  const all = chart.series.flatMap((s) => s.values).filter((v) => typeof v === 'number');
  const scatter = chart.type === 'scatter';
  const xs = scatter ? chart.labels : chart.labels.map((_, i) => i);
  const [yMin, yMax, yTicks] = niceTicks(Math.min(0, ...all), Math.max(...all, 0));
  const [xMin, xMax, xTicks] = scatter ? niceTicks(Math.min(...xs), Math.max(...xs)) : [0, Math.max(1, xs.length), null];
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const y = (v) => PAD.top + plotH - ((v - yMin) / (yMax - yMin || 1)) * plotH;
  const band = plotW / Math.max(1, chart.labels.length);
  const x = (v, i) => (scatter ? PAD.left + ((v - xMin) / (xMax - xMin || 1)) * plotW : PAD.left + band * (i + 0.5));
  const n = chart.series.length;
  const barW = Math.min(36, (band * 0.7) / n);
  const tick = 'fill-[rgb(var(--cream-muted))] text-[11px]';
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={chart.title || 'chart'}>
      {yTicks.map((v) => (
        <g key={v}>
          <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} stroke="rgb(var(--line) / 0.08)" />
          <text x={PAD.left - 6} y={y(v) + 4} textAnchor="end" className={tick}>{Number(v.toPrecision(4))}</text>
        </g>
      ))}
      {scatter ? xTicks.map((v) => (
        <text key={v} x={x(v)} y={H - PAD.bottom + 16} textAnchor="middle" className={tick}>{Number(v.toPrecision(4))}</text>
      )) : chart.labels.map((label, i) => (
        <text key={i} x={x(0, i)} y={H - PAD.bottom + 16} textAnchor="middle" className={tick}>
          {String(label).length > 14 ? `${String(label).slice(0, 13)}…` : label}
        </text>
      ))}
      {chart.x_label && <text x={PAD.left + plotW / 2} y={H - 6} textAnchor="middle" className={tick}>{chart.x_label}</text>}
      {chart.y_label && <text x={12} y={PAD.top + plotH / 2} textAnchor="middle" className={tick}
        transform={`rotate(-90 12 ${PAD.top + plotH / 2})`}>{chart.y_label}</text>}
      {chart.series.map((s, si) => {
        const color = COLORS[si % COLORS.length];
        if (chart.type === 'bar') {
          return s.values.map((v, i) => (typeof v === 'number' ? (
            <rect key={`${si}-${i}`} x={x(0, i) - (barW * n) / 2 + barW * si} width={barW - 2}
              y={Math.min(y(v), y(0))} height={Math.abs(y(v) - y(0))} fill={color} rx="2">
              <title>{`${s.name ? `${s.name} · ` : ''}${chart.labels[i]}: ${v}`}</title>
            </rect>) : null));
        }
        const points = s.values.map((v, i) => (typeof v === 'number' ? [x(xs[i], i), y(v), v, i] : null)).filter(Boolean);
        return (
          <g key={si}>
            {chart.type === 'line' && <polyline points={points.map((p) => `${p[0]},${p[1]}`).join(' ')} fill="none" stroke={color} strokeWidth="2" />}
            {points.map((p) => (
              <circle key={p[3]} cx={p[0]} cy={p[1]} r={chart.type === 'line' ? 3 : 4} fill={color}>
                <title>{`${s.name ? `${s.name} · ` : ''}${chart.labels[p[3]]}: ${p[2]}`}</title>
              </circle>
            ))}
          </g>
        );
      })}
    </svg>
  );
}

function Chart({ source }) {
  const chart = useMemo(() => parseChart(source), [source]);
  if (!chart) {
    return <pre className="my-3 overflow-x-auto rounded-lg bg-ink-surface p-3 font-mono text-[13px] text-cream-secondary"><code>{source}</code></pre>;
  }
  return (
    <figure className="my-3 rounded-xl border border-border-subtle bg-ink-raised p-4">
      {chart.title && <figcaption className="mb-2 text-[14px] font-medium text-cream">{chart.title}</figcaption>}
      {chart.type === 'pie' ? <Pie chart={chart} /> : <><Axes chart={chart} /><Legend series={chart.series} /></>}
    </figure>
  );
}

export default memo(Chart);
