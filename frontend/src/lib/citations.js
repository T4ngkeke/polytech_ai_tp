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
