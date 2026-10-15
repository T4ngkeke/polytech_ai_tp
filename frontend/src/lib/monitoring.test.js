import { test } from 'node:test';
import assert from 'node:assert/strict';

import { dashboardUrl, grafanaState } from './monitoring.js';

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

test('Grafana is running only when its health check answers; 401 means not allowed', async () => {
  const answer = (status, body) => async () => ({ ok: status < 400, status, json: async () => body });
  assert.equal(await grafanaState(answer(200, { database: 'ok' })), 'running');
  assert.equal(await grafanaState(answer(502, {})), 'stopped');
  assert.equal(await grafanaState(answer(401, {})), 'denied');
  // An older frontend returns the app page: not JSON.
  assert.equal(await grafanaState(async () => ({ ok: true, status: 200, json: async () => { throw new SyntaxError(); } })), 'stopped');
  assert.equal(await grafanaState(async () => { throw new TypeError('network'); }), 'stopped');
});
