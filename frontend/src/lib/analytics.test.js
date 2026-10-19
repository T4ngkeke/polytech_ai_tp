import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  busiestHour, canRefill, classesByTeacher, heatLevel, lastActive, lastDays, lastWeek, questionCount, quotaShare,
  shortDay, sortClasses, sortStudents, tipShift,
} from './analytics.js';

test('question counts read as words', () => {
  assert.equal(questionCount(0), '0 questions');
  assert.equal(questionCount(1), '1 question');
  assert.equal(questionCount(7), '7 questions');
});

test('a tooltip near an edge of the chart stays inside it', () => {
  assert.equal(tipShift(5, 400), '0%');
  assert.equal(tipShift(200, 400), '-50%');
  assert.equal(tipShift(395, 400), '-100%');
});

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

test('the platform chart has every one of the last days, empty ones at zero', () => {
  const days = lastDays([{ date: '2026-10-18', requests: 4 }, { date: '2026-09-01', requests: 9 }],
    3, new Date(2026, 9, 19));
  assert.deepEqual(days, [
    { date: '2026-10-17', questions: 0 },
    { date: '2026-10-18', questions: 4 },
    { date: '2026-10-19', questions: 0 },
  ]);
});

test('the last week adds up the last seven days only', () => {
  const days = Array.from({ length: 10 }, (_, i) => ({ date: `d${i}`, questions: 1 }));
  assert.equal(lastWeek(days), 7);
});

test('classes are grouped by the teacher who owns them', () => {
  const owned = classesByTeacher([
    { teacher_id: 't1', name: 'A' }, { teacher_id: 't2', name: 'B' }, { teacher_id: 't1', name: 'C' },
  ]);
  assert.deepEqual(owned, { t1: ['A', 'C'], t2: ['B'] });
});

test('classes sort by teacher, then by name', () => {
  const rows = [
    { name: 'Z', teacher_username: 'bob', student_count: 1, questions_7d: 0, last_active: null },
    { name: 'A', teacher_username: 'bob', student_count: 5, questions_7d: 3, last_active: '2026-10-18T10:00:00Z' },
    { name: 'M', teacher_username: 'alice', student_count: 2, questions_7d: 9, last_active: null },
  ];
  assert.deepEqual(sortClasses(rows, 'teacher').map((c) => c.name), ['M', 'A', 'Z']);
  assert.deepEqual(sortClasses(rows, 'questions_7d', 'desc').map((c) => c.name), ['M', 'A', 'Z']);
  assert.deepEqual(sortClasses(rows, 'last_active', 'desc').map((c) => c.name), ['A', 'M', 'Z']);
});
