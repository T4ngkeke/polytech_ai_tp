import { test } from 'node:test';
import assert from 'node:assert/strict';

import { dashboardUrl, grafanaRunning } from './monitoring.js';

test('an embedded dashboard hides the Grafana menus and follows the app theme', () => {
  const url = dashboardUrl('system', { theme: 'dark' });
  assert.ok(url.startsWith('/grafana/d/edullm-system/?'));
  assert.match(url, /theme=dark/);
  assert.match(url, /&kiosk$/);
});

test('the full Grafana page keeps its menus; an unknown dashboard opens System', () => {
  assert.equal(dashboardUrl('answers', { embedded: false }), '/grafana/d/edullm-answers/?orgId=1&theme=light');
  assert.match(dashboardUrl('nope'), /edullm-system/);
});

test('Grafana is running only when its health check answers', async () => {
  const answer = (ok, body) => async () => ({ ok, json: async () => body });
  assert.equal(await grafanaRunning(answer(true, { database: 'ok' })), true);
  assert.equal(await grafanaRunning(answer(false, {})), false);
  // An older frontend returns the app page: not JSON.
  assert.equal(await grafanaRunning(async () => ({ ok: true, json: async () => { throw new SyntaxError(); } })), false);
  assert.equal(await grafanaRunning(async () => { throw new TypeError('network'); }), false);
});
