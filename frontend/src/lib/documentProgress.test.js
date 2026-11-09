import { test } from 'node:test';
import assert from 'node:assert/strict';

import { displayStatus, readingLine, statusGroup, timeLeft } from './documentProgress.js';

test('the time left reads in minutes', () => {
  assert.equal(timeLeft(null), null);
  assert.equal(timeLeft(40), 'less than a minute left');
  assert.equal(timeLeft(480), 'about 8 min left');
});

test('a queued document says how many are ahead', () => {
  assert.deepEqual(readingLine('pending', { jobs_ahead: 0 }), { text: 'Waiting to start', share: 0 });
  assert.equal(readingLine('pending', { jobs_ahead: 1 }).text, 'Waiting: 1 document ahead');
  assert.equal(readingLine('pending', { jobs_ahead: 3 }).text, 'Waiting: 3 documents ahead');
});

test('a document being read shows its pages and, once known, the time left', () => {
  assert.equal(readingLine('processing', {}).text, 'Opening the PDF…');
  assert.equal(readingLine('processing', { progress: { done: 2, total: 40, seconds_left: null } }).text,
    '2 of 40 pages read');
  const line = readingLine('processing', { progress: { done: 20, total: 40, seconds_left: 300 } });
  assert.equal(line.text, '20 of 40 pages read · about 5 min left');
  assert.equal(line.share, 0.475);
  assert.equal(readingLine('parsed', {}).text, 'Indexing…');
  assert.equal(readingLine('indexed', {}), null);
});

test('an unpublished structured document shows its newest revision', () => {
  const draft = { status: 'pending', structured: true, current_revision_id: null };
  assert.equal(displayStatus({ ...draft, latest_revision: { processing_state: 'failed' } }), 'failed');
  assert.equal(displayStatus({ ...draft, latest_revision: { processing_state: 'partial' } }), 'needs_review');
  assert.equal(displayStatus({ ...draft, latest_revision: { processing_state: 'queued' } }), 'pending');
  // Published, or an older upload: the document's own status.
  assert.equal(displayStatus({ ...draft, current_revision_id: 'r1', status: 'indexed',
    latest_revision: { processing_state: 'failed' } }), 'indexed');
  assert.equal(displayStatus({ status: 'failed', structured: false }), 'failed');
});

test('statuses fall in three groups', () => {
  assert.equal(statusGroup('processing'), 'working');
  assert.equal(statusGroup('parsed'), 'working');
  assert.equal(statusGroup('failed'), 'problem');
  assert.equal(statusGroup('partially_indexed'), 'problem');
  assert.equal(statusGroup('indexed'), 'ready');
});
