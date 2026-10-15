import { test } from 'node:test';
import assert from 'node:assert/strict';

import { busiestHour, canRefill, heatLevel, lastActive, quotaShare, shortDay, sortStudents } from './analytics.js';

test('an hour with questions is never blank; the busiest is the darkest', () => {
  assert.equal(heatLevel(0, 10), 0);
  assert.equal(heatLevel(1, 10), 1);
  assert.equal(heatLevel(10, 10), 4);
  assert.equal(heatLevel(3, 0), 0);
});

test('the busiest hour names its weekday', () => {
  const grid = Array.from({ length: 7 }, () => Array(24).fill(0));
  assert.equal(busiestHour(grid), null);
  grid[1][14] = 5;
  grid[3][9] = 2;
  assert.deepEqual(busiestHour(grid), { day: 'Tue', hour: 14, count: 5 });
});

test('last active counts calendar days', () => {
  const now = new Date(2026, 9, 15, 9, 0);
  assert.equal(lastActive(null, now), 'never');
  assert.equal(lastActive(new Date(2026, 9, 15, 8).toISOString(), now), 'today');
  assert.equal(lastActive(new Date(2026, 9, 14, 23).toISOString(), now), 'yesterday');
  assert.equal(lastActive(new Date(2026, 9, 10, 12).toISOString(), now), '5 days ago');
});

test('refill is offered only when it would give tokens back', () => {
  assert.equal(quotaShare(150, 100), 1);
  assert.equal(canRefill({ member: true, used_today: 100, granted_today: 0 }), true);
  assert.equal(canRefill({ member: true, used_today: 100, granted_today: 100 }), false);
  assert.equal(canRefill({ member: false, used_today: 100, granted_today: 0 }), false);
});

test('students sort by a column, then by name', () => {
  const rows = [
    { username: 'bob', questions: 2, last_active: null, used_today: 0, limit_today: 10, thumbs_down: 0 },
    { username: 'amy', questions: 2, last_active: '2026-10-14T10:00:00Z', used_today: 9, limit_today: 10, thumbs_down: 1 },
    { username: 'cat', questions: 5, last_active: '2026-10-10T10:00:00Z', used_today: 1, limit_today: 10, thumbs_down: 0 },
  ];
  assert.deepEqual(sortStudents(rows, 'questions').map((r) => r.username), ['cat', 'amy', 'bob']);
  assert.deepEqual(sortStudents(rows, 'today').map((r) => r.username), ['amy', 'cat', 'bob']);
  assert.deepEqual(sortStudents(rows, 'username', 'asc').map((r) => r.username), ['amy', 'bob', 'cat']);
});

test('a day keeps its date in every time zone', () => {
  assert.equal(shortDay('2026-10-13'), '13 Oct');
});
