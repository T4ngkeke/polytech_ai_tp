import { test } from 'node:test';
import assert from 'node:assert/strict';

import { boxStyle, cropStyle, pictureNumbers, searchNote, visibleIssues, withoutText } from './documentBlocks.js';

const picture = (over = {}) => ({ block_id: 'f', type: 'figure', content: '', index_state: 'needs_review',
  edited_by_teacher: false, issues: [{ code: 'content_review', message: 'Figure has no native PDF labels' }], ...over });

test('pictures are numbered in reading order', () => {
  const blocks = [{ block_id: 't', type: 'text' }, { block_id: 'a', type: 'figure' }, { block_id: 'b', type: 'figure' }];
  assert.deepEqual(pictureNumbers(blocks), { a: 1, b: 2 });
});

test('a picture without text is said plainly, not as a warning', () => {
  assert.equal(withoutText(picture()), true);
  assert.equal(searchNote(picture()), 'no text · not searched');
  assert.deepEqual(visibleIssues(picture()), []);
  assert.equal(searchNote(picture({ index_state: 'excluded' })), 'left out of search');
  assert.equal(searchNote(picture({ content: 'Schéma bloc', index_state: 'searchable', edited_by_teacher: true })),
    'described');
  assert.equal(searchNote({ type: 'text', content: 'x', index_state: 'searchable', issues: [] }), null);
  const other = { code: 'extraction_failure', message: 'MinerU failed' };
  assert.deepEqual(visibleIssues(picture({ issues: [other, { code: 'source_geometry_unverified' }] })), [other]);
});

test('a region is placed over the preview and cropped out of it', () => {
  assert.deepEqual(boxStyle([100, 200, 600, 700]), { left: '10%', top: '20%', width: '50%', height: '50%' });
  // Half the width and half the height of a 4:3 page: a 4:3 frame, the image twice as wide.
  const crop = cropStyle([500, 500, 1000, 1000], 4 / 3);
  assert.equal(crop.frame.aspectRatio, '666.667 / 500');
  assert.deepEqual(crop.image, { width: '200.00%', left: '-100.00%', top: '-100.00%' });
});
