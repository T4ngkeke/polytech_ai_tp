/**
 * The Grafana dashboards shown in the admin Monitoring tab. Grafana is served
 * by the same site under /grafana/ (frontend/nginx.conf); the dashboards are
 * the files in monitoring/grafana/dashboards.
 */

export const DASHBOARDS = [
  { key: 'system', uid: 'edullm-system', label: 'System', hint: 'Machine, GPU, model servers, containers' },
  { key: 'answers', uid: 'edullm-answers', label: 'Answers', hint: 'Questions, answer times, model calls, documents' },
];

/**
 * The URL of one dashboard. `embedded` hides Grafana's menus (kiosk mode);
 * the time range and the filters stay at the top of the dashboard.
 */
export function dashboardUrl(key, { theme = 'light', embedded = true } = {}) {
  const board = DASHBOARDS.find((d) => d.key === key) || DASHBOARDS[0];
  const params = new URLSearchParams({ orgId: '1', theme });
  return `/grafana/d/${board.uid}/?${params.toString()}${embedded ? '&kiosk' : ''}`;
}

/**
 * 'running', 'denied' (nginx let the request through only for an app admin
 * with the monitoring cookie: 401) or 'stopped'. Without the monitoring
 * containers, /grafana/ returns a 502, or the app page on an older frontend.
 */
export async function grafanaState(fetchFn = fetch) {
  try {
    const res = await fetchFn('/grafana/api/health');
    if (res.status === 401) return 'denied';
    if (!res.ok) return 'stopped';
    const body = await res.json();
    return body?.database === 'ok' ? 'running' : 'stopped';
  } catch {
    return 'stopped';
  }
}
