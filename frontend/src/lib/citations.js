/**
 * Group an answer's citations for display: one line per course file, one
 * button per page. A research answer cites passages (blocks, ranges), often
 * several on one page; the student needs the pages, and which [E#] each one
 * holds to match the markers in the answer text.
 */

const labelNumber = (label) => Number(String(label || '').replace(/^E/, '')) || 0;

/**
 * @returns {Array<{key, documentId, revisionId, filename, newerRevision,
 *   pages: Array<{pageNo, labels, available, teacherRevised}>}>}
 * Files keep the order of their first citation; pages are ascending. A page
 * is available only if all its citations are (a revoked source is never
 * offered). Legacy citations without a PDF page are left out.
 */
export function groupCitations(citations = []) {
  const files = new Map();
  for (const c of citations) {
    const pageNo = c.pdf_page_no ?? c.page_no;
    if (!c.document_id || !Number.isInteger(pageNo) || pageNo < 1) continue;
    const key = `${c.document_id}:${c.revision_id || ''}`;
    if (!files.has(key)) {
      files.set(key, { key, documentId: c.document_id, revisionId: c.revision_id || null,
        filename: c.filename || 'Source', newerRevision: false, pages: new Map() });
    }
    const file = files.get(key);
    file.newerRevision = file.newerRevision || Boolean(c.newer_revision);
    if (!file.pages.has(pageNo)) {
      file.pages.set(pageNo, { pageNo, labels: [], available: true, teacherRevised: false });
    }
    const page = file.pages.get(pageNo);
    if (c.label && !page.labels.includes(c.label)) page.labels.push(c.label);
    // Agent citations say whether they are still readable; legacy ones do not.
    page.available = page.available && (c.citation_id ? Boolean(c.available) : true);
    page.teacherRevised = page.teacherRevised || Boolean(c.teacher_revised);
  }
  return [...files.values()].map((file) => ({
    ...file,
    pages: [...file.pages.values()]
      .map((page) => ({ ...page, labels: [...page.labels].sort((a, b) => labelNumber(a) - labelNumber(b)) }))
      .sort((a, b) => a.pageNo - b.pageNo),
  }));
}

/** One preview card per original image (an image can be cited several times). */
export function distinctImages(citations = []) {
  const seen = new Set();
  return citations.filter((c) => {
    if (c.origin !== 'live_visual' || !c.available || !c.asset_id || !c.asset_mime?.startsWith('image/')) return false;
    if (seen.has(c.asset_id)) return false;
    seen.add(c.asset_id);
    return true;
  });
}

const GROUP = String.raw`\[E[1-9]\d*(?:\s*[,;]\s*E[1-9]\d*)*\]`;
// Groups written side by side on one line: "[E3][E5]", "[E3, E5] [E7]".
const CITATION_RUN = new RegExp(`${GROUP}(?:[ \\t]*${GROUP})*`, 'g');

/** One page's labels as one name: E21–22, E13–15·17, E11·18 (a comma would read as two links). */
export function labelRange(ids) {
  const numbers = [...new Set(ids.map(labelNumber))].sort((a, b) => a - b);
  const runs = [];
  for (const n of numbers) {
    const last = runs[runs.length - 1];
    if (last && n === last[1] + 1) last[1] = n;
    else runs.push([n, n]);
  }
  return `E${runs.map(([a, b]) => (a === b ? `${a}` : `${a}–${b}`)).join('·')}`;
}

const link = (pages) => `\\[${pages.map((ids) => `[${labelRange(ids)}](#cite-${ids.join(',')})`).join(', ')}\\]`;

/**
 * Turn the answer's [E#] markers into links (#cite-E5) the chat renders as
 * buttons, so a student can open the cited passage from the sentence. Only
 * labels of this answer's citations are linked; code is left as it is.
 * Groups side by side become one; with pageOf (label -> page key), labels of
 * one page become one link named by their range ([E21–22](#cite-E21,E22)).
 */
export function linkCitations(markdown, labels, pageOf = null) {
  if (!markdown || !labels?.size) return markdown;
  // Odd parts are code (fenced or inline); only even parts are prose.
  return markdown.split(/(```[\s\S]*?```|`[^`\n]*`)/).map((part, i) => (i % 2 ? part
    : part.replace(CITATION_RUN, (run) => {
      const groups = run.match(new RegExp(GROUP, 'g')).map((g) => g.slice(1, -1).split(/\s*[,;]\s*/));
      if (!groups.flat().every((id) => labels.has(id))) {
        // An unknown label stays as written, its group unlinked; the others are linked.
        return run.replace(new RegExp(GROUP, 'g'), (whole) => {
          const list = whole.slice(1, -1).split(/\s*[,;]\s*/);
          return list.every((id) => labels.has(id)) ? link(list.map((id) => [id])) : whole;
        });
      }
      const pages = new Map();
      for (const id of new Set(groups.flat())) {
        const key = pageOf?.get(id) ?? id;
        if (!pages.has(key)) pages.set(key, []);
        pages.get(key).push(id);
      }
      return link([...pages.values()]);
    }))).join('');
}

/** The highlight boxes of one page: cited regions, merged when two labels share one. */
export function pageHighlights(citations = [], pageNo, focusLabels = null) {
  const boxes = new Map();
  for (const c of citations) {
    if ((c.pdf_page_no ?? c.page_no) !== pageNo || !Array.isArray(c.box) || c.box.length !== 4) continue;
    if (focusLabels && !focusLabels.includes(c.label)) continue;
    const key = c.box.join(',');
    if (!boxes.has(key)) boxes.set(key, { box: c.box, labels: [] });
    if (c.label && !boxes.get(key).labels.includes(c.label)) boxes.get(key).labels.push(c.label);
  }
  return [...boxes.values()];
}
