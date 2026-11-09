import { test } from 'node:test';
import assert from 'node:assert/strict';

import { blockedMessage, nearLimit, nearLimitMessage, percent, quotaRefusal, resetIn } from './quota.js';

const NOW = new Date('2026-11-09T17:00:00Z');

test('percent is capped and safe without a limit', () => {
  assert.equal(percent(50, 200), 25);
  assert.equal(percent(300, 200), 100);
  assert.equal(percent(5, 0), 0);
});

test('resetIn speaks in minutes, hours or days', () => {
  assert.equal(resetIn('2026-11-09T17:20:00Z', NOW), 'in 20 min');
  assert.equal(resetIn('2026-11-09T23:00:00Z', NOW), 'in 6 h');
  assert.equal(resetIn('2026-11-16T23:00:00Z', NOW), 'in 7 days');
  assert.equal(resetIn('2026-11-09T16:00:00Z', NOW), 'soon');
});

test('blockedMessage names the limit and when it resets', () => {
  assert.equal(blockedMessage({ blocked: null }), null);
  assert.equal(blockedMessage({ blocked: 'day', resets_at: '2026-11-09T23:00:00Z' }, NOW),
    "You have reached today's limit. It resets at midnight (in 6 h).");
  assert.equal(blockedMessage({ blocked: 'week', week_resets_at: '2026-11-15T23:00:00Z' }, NOW),
    "You have reached this week's limit. It resets on Monday (in 6 days).");
});

test('nearLimit warns at 80% once per day or week, never when blocked', () => {
  const base = { used: 10, limit: 100, week_used: 10, week_limit: 1000, blocked: null,
    resets_at: 'd1', week_resets_at: 'w1' };
  assert.equal(nearLimit(base), null);
  assert.deepEqual(nearLimit({ ...base, used: 80 }), { kind: 'day', key: 'quota80:day:d1' });
  assert.deepEqual(nearLimit({ ...base, week_used: 900 }), { kind: 'week', key: 'quota80:week:w1' });
  assert.equal(nearLimit({ ...base, used: 100, blocked: 'day' }), null);
  assert.match(nearLimitMessage('week'), /this week/);
});

test('quotaRefusal reads the server detail', () => {
  assert.equal(quotaRefusal('week_quota_reached'), "You have reached this week's limit.");
  assert.equal(quotaRefusal('day_quota_reached'), "You have reached today's limit.");
});
