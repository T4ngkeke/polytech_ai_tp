import { test } from 'node:test';
import assert from 'node:assert/strict';

import { distinctImages, groupCitations } from './citations.js';

const cite = (label, page, extra = {}) => ({
  citation_id: `c-${label}`, label, document_id: 'doc-a', revision_id: 'rev-1',
  filename: 'Theorie.pdf', pdf_page_no: page, available: true, ...extra,
});

test('twelve passages on three pages of one file become one line with three pages', () => {
  const citations = [cite('E31', 22), cite('E27', 22), cite('E35', 24), cite('E28', 22),
    cite('E11', 23), cite('E37', 24)];
  const groups = groupCitations(citations);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].filename, 'Theorie.pdf');
  assert.deepEqual(groups[0].pages.map((p) => [p.pageNo, p.labels]),
    [[22, ['E27', 'E28', 'E31']], [23, ['E11']], [24, ['E35', 'E37']]]);
});

test('files keep the order of their first citation', () => {
  const groups = groupCitations([cite('E1', 5, { document_id: 'doc-b', filename: 'SQL.pdf' }),
    cite('E2', 3), cite('E3', 7, { document_id: 'doc-b', filename: 'SQL.pdf' })]);
  assert.deepEqual(groups.map((g) => [g.filename, g.pages.map((p) => p.pageNo)]),
    [['SQL.pdf', [5, 7]], ['Theorie.pdf', [3]]]);
});

test('a page with one revoked citation is not offered', () => {
  const [file] = groupCitations([cite('E1', 4), cite('E2', 4, { available: false }), cite('E3', 5)]);
  assert.deepEqual(file.pages.map((p) => [p.pageNo, p.available]), [[4, false], [5, true]]);
});

test('older revisions and teacher edits are flagged; two revisions stay apart', () => {
  const groups = groupCitations([cite('E1', 2, { newer_revision: true }),
    cite('E2', 2, { revision_id: 'rev-2', teacher_revised: true })]);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].newerRevision, true);
  assert.equal(groups[1].pages[0].teacherRevised, true);
});

test('legacy citations use page_no; ones without a page are left out', () => {
  const groups = groupCitations([{ document_id: 'doc-a', filename: 'Old.pdf', page_no: 9 },
    { document_id: 'doc-a', filename: 'Old.pdf' }]);
  assert.deepEqual(groups[0].pages, [{ pageNo: 9, labels: [], available: true, teacherRevised: false }]);
});

test('an image cited twice gets one preview card', () => {
  const image = { origin: 'live_visual', available: true, asset_id: 'img-1', asset_mime: 'image/png' };
  const cards = distinctImages([cite('E1', 2, image), cite('E2', 2, image),
    cite('E3', 3, { ...image, asset_id: 'img-2' }), cite('E4', 3)]);
  assert.deepEqual(cards.map((c) => c.label), ['E1', 'E3']);
});
