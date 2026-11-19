/**
 * chat/QuotaBar.jsx — Meter for the tokens used today or this week.
 */
import { percent } from '../../lib/quota';

export default function QuotaBar({ label, used, limit }) {
  const pct = percent(used, limit);
  return (
    <div>
      <div className="flex justify-between text-[10px] text-cream-muted mb-1">
        <span>{label}</span>
        <span className="font-mono">{pct}%</span>
      </div>
      <div className="h-1 bg-ink-surface rounded-full overflow-hidden"
        role="meter" aria-label={`${label}: ${pct}% of the limit used`} aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className={`h-full rounded-full transition-all duration-500 ${
          pct >= 100 ? 'bg-danger' : pct >= 80 ? 'bg-gold' : 'bg-cyan'}`}
          style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
