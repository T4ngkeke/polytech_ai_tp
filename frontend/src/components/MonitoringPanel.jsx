/**
 * MonitoringPanel — the admin Monitoring tab: the Grafana dashboards, shown
 * inside the app without Grafana's menus. Grafana draws the charts and keeps
 * the history (see documentation/MONITORING.md).
 */
import { useEffect, useState } from 'react';
import { ExternalLink } from 'lucide-react';
import { DASHBOARDS, dashboardUrl, grafanaRunning } from '../lib/monitoring';
import { useTheme } from '../lib/theme';

export default function MonitoringPanel() {
  const theme = useTheme();
  const [board, setBoard] = useState('system');
  const [running, setRunning] = useState(null); // null while checking

  useEffect(() => {
    let live = true;
    grafanaRunning().then((ok) => { if (live) setRunning(ok); });
    return () => { live = false; };
  }, []);

  const hint = DASHBOARDS.find((d) => d.key === board)?.hint;

  return (
    <div className="h-full flex flex-col p-4 md:p-8 gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-display text-cream">Monitoring</h2>
          <p className="text-xs text-cream-muted mt-1">{hint}</p>
        </div>
        {running && (
          <a href={dashboardUrl(board, { theme, embedded: false })} target="_blank" rel="noreferrer"
            className="inline-flex items-center gap-1.5 text-xs text-cream-muted hover:text-cyan">
            Open in Grafana <ExternalLink className="w-3.5 h-3.5" aria-hidden="true" />
          </a>
        )}
      </div>

      <div>
        <Segmented items={DASHBOARDS} value={board} onChange={setBoard} />
      </div>

      <div className="flex-1 min-h-0 rounded-xl border border-border-subtle bg-ink-base overflow-hidden">
        {running === null && <p className="p-6 text-sm text-cream-muted">Checking monitoring…</p>}
        {running === false && <NotRunning />}
        {running && (
          <iframe key={`${board}-${theme}`} title={`${board} dashboard`} src={dashboardUrl(board, { theme })}
            className="w-full h-full border-0" />
        )}
      </div>
    </div>
  );
}

function Segmented({ items, value, onChange }) {
  return (
    <div className="inline-flex rounded-lg border border-border-subtle bg-ink-base p-0.5">
      {items.map((item) => (
        <button key={item.key} type="button" onClick={() => onChange(item.key)}
          className={`px-3 py-1 rounded-md text-xs font-medium transition-colors cursor-pointer ${
            value === item.key ? 'bg-cyan-muted text-cyan' : 'text-cream-secondary hover:text-cream'
          }`}>
          {item.label}
        </button>
      ))}
    </div>
  );
}

function NotRunning() {
  return (
    <div className="p-6 max-w-xl space-y-3 text-sm text-cream-secondary">
      <p className="text-cream font-medium">Monitoring is not running.</p>
      <p>
        The charts come from Grafana and Prometheus, which run next to the app. Start them on the
        server with:
      </p>
      <pre className="text-xs bg-ink-deep rounded-lg p-3 overflow-x-auto">
        docker compose up -d grafana prometheus node-exporter gpu-exporter cadvisor
      </pre>
      <p className="text-xs text-cream-muted">See documentation/MONITORING.md.</p>
    </div>
  );
}
