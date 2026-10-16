/**
 * Pure helpers of the teacher's document page view (components/DocumentBlockEditor.jsx):
 * block names in words, how search treats a block, and where a block is on the
 * page preview. `region` is MinerU's box [x0, y0, x1, y1] in thousandths of
 * the page width and height (null when unknown).
 */

export const KIND_NAME = {
  heading: 'Heading', text: 'Text', code: 'Code', formula: 'Formula', table: 'Table',
  figure: 'Picture', navigation: 'Navigation',
};

/** Pictures of a page numbered in reading order: { block_id: 1, … }. */
export function pictureNumbers(blocks) {
  const numbers = {};
  blocks.filter((b) => b.type === 'figure').forEach((b, i) => { numbers[b.block_id] = i + 1; });
  return numbers;
}

/** A block with no text that search skips (a photo, a diagram without labels). */
export function withoutText(block) {
  return block.index_state === 'needs_review' && !(block.content || '').trim();
}

/** How search treats the block, in words; null for an ordinary searchable block. */
export function searchNote(block) {
  if (block.index_state === 'excluded') return 'left out of search';
  if (withoutText(block)) return 'no text · not searched';
  if (block.index_state === 'needs_review') return 'not searched';
  if (block.type === 'figure' && block.edited_by_teacher) return 'described';
  return null;
}

/**
 * Issues worth showing on the block. A picture without text is said by
 * searchNote; a box MinerU placed loosely only matters to the details.
 */
export function visibleIssues(block) {
  return (block.issues || []).filter((i) => i.code !== 'source_geometry_unverified'
    && !(i.code === 'content_review' && block.type === 'figure'));
}

/** Position of a region over the page preview, in % of the preview. */
export function boxStyle(region) {
  const [x0, y0, x1, y1] = region;
  return { left: `${x0 / 10}%`, top: `${y0 / 10}%`, width: `${(x1 - x0) / 10}%`, height: `${(y1 - y0) / 10}%` };
}

/**
 * Show only the region of the page preview: the frame has the region's shape
 * (pageRatio = page width / height), the image is scaled and moved inside it.
 */
export function cropStyle(region, pageRatio) {
  const [x0, y0, x1, y1] = region;
  const w = x1 - x0;
  const h = y1 - y0;
  return {
    frame: { aspectRatio: `${(w * pageRatio).toFixed(3)} / ${h}` },
    image: { width: `${(100000 / w).toFixed(2)}%`, left: `${(-100 * x0 / w).toFixed(2)}%`,
             top: `${(-100 * y0 / h).toFixed(2)}%` },
  };
}
