/**
 * MonitoringPanel — the admin Monitoring tab: the Grafana dashboards, shown
 * inside the app without Grafana's menus. Grafana draws the charts and keeps
 * the history (see documentation/MONITORING.md). Grafana is open to app
 * admins only: the tab first gets the monitoring cookie nginx checks.
 */
import { useEffect, useState } from 'react';
import { ExternalLink } from 'lucide-react';
import api from '../lib/api';
import { DASHBOARDS, dashboardUrl, grafanaState } from '../lib/monitoring';
import { useTheme } from '../lib/theme';
import { useT } from '../lib/i18n';

export default function MonitoringPanel() {
  const theme = useTheme();
  const t = useT();
  const [board, setBoard] = useState('system');
  const [state, setState] = useState(null); // null while checking; 'running', 'denied', 'stopped'
  const running = state === 'running';

  useEffect(() => {
    let live = true;
    api.post('/api/admin/monitoring/session')
      .then(() => grafanaState(), () => 'denied')
      .then((s) => { if (live) setState(s); });
    return () => { live = false; };
  }, []);

  const hint = DASHBOARDS.some((d) => d.key === board) ? t(`a.mon.${board}.hint`) : '';

  return (
    <div className="h-full flex flex-col p-4 md:p-8 gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-[18px] font-semibold text-cream">{t('a.nav.monitoring')}</h2>
          <p className="text-[13px] text-cream-muted mt-1">{hint}</p>
        </div>
        {running && (
          <a href={dashboardUrl(board, { theme, embedded: false })} target="_blank" rel="noreferrer"
            className="inline-flex items-center gap-1.5 text-[13px] text-cream-muted hover:text-cream">
            {t('a.mon.open')} <ExternalLink className="w-3.5 h-3.5" aria-hidden="true" />
          </a>
        )}
      </div>

      <div>
        <Segmented items={DASHBOARDS} value={board} onChange={setBoard} />
      </div>

      <div className="flex-1 min-h-0 rounded-2xl border border-border-subtle bg-ink-raised overflow-hidden">
        {state === null && <p className="p-6 text-[14px] text-cream-muted">{t('a.mon.checking')}</p>}
        {state === 'stopped' && <NotRunning />}
        {state === 'denied' && (
          <p className="p-6 text-[14px] text-cream-secondary">{t('a.mon.denied')}</p>
        )}
        {running && (
          <iframe key={`${board}-${theme}`} title={t('a.mon.frame', { name: t(`a.mon.${board}`) })} src={dashboardUrl(board, { theme })}
            className="w-full h-full border-0" />
        )}
      </div>
    </div>
  );
}

function Segmented({ items, value, onChange }) {
  const t = useT();
  return (
    <div className="inline-flex max-w-full flex-wrap rounded-xl border border-border-subtle bg-ink-raised p-0.5">
      {items.map((item) => (
        <button key={item.key} type="button" onClick={() => onChange(item.key)}
          className={`px-3 py-1 rounded-lg text-[13px] transition-colors cursor-pointer ${
            value === item.key ? 'bg-ink-hover text-cream' : 'text-cream-secondary hover:text-cream'
          }`}>
          {t(`a.mon.${item.key}`)}
        </button>
      ))}
    </div>
  );
}

function NotRunning() {
  const t = useT();
  return (
    <div className="p-6 max-w-xl space-y-3 text-[14px] text-cream-secondary">
      <p className="text-cream font-medium">{t('a.mon.stopped')}</p>
      <p>{t('a.mon.stoppedHelp')}</p>
      <pre className="text-[12.5px] bg-ink-deep rounded-xl p-3 overflow-x-auto">
        docker compose up -d grafana prometheus node-exporter gpu-exporter cadvisor
      </pre>
      <p className="text-[13px] text-cream-muted">{t('a.mon.see')}</p>
    </div>
  );
}
