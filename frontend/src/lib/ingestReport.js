// ingestReport.js — [v8.0] turn a document's ingest_report into teacher warnings.
//
// Two report shapes share this (mutually exclusive per the strict split):
//   CM      → { chunk_count, ingest_tokens_est, token_budget, over_budget }
//   TD/TP   → { anomaly, gaps, collisions, segmenter, boundary_disagreements,
//               dropped_invalid_lines, exercise_count }
// Kept in one place so the frontend contract can't silently drift from the
// worker's report keys (as it did when `resegmented` was renamed to `segmenter`).

/**
 * @param {object|null|undefined} report
 * @returns {string[]} human-readable warnings (empty when the ingest looks clean)
 */
export function ingestReportWarnings(report) {
  if (!report) return [];
  const warnings = [];

  if (report.anomaly) warnings.push(`Numbering anomaly: ${report.anomaly}`);
  if (report.gaps?.length) warnings.push(`Missing numbers: ${report.gaps.join(', ')}`);
  if (report.collisions?.length) {
    warnings.push(`In-lab number collisions: ${report.collisions.join(', ')}`);
  }
  // A degraded segmentation: the per-page LLM classifier failed and the worker
  // fell back to the regex segmenter, so numbering may be less reliable.
  if (report.segmenter === 'regex_fallback') {
    warnings.push('Exercise segmentation fell back to regex (the line classifier failed); numbering may be less accurate.');
  }
  if (report.over_budget) {
    warnings.push(`Ingest token budget exceeded (~${report.ingest_tokens_est} est. > ${report.token_budget})`);
  }
  // [v8.1] The two segmenters disagreed on the exercise boundaries; both
  // candidates are stored — the teacher compares and confirms one below.
  if (report.segmentation_disagreement && !report.segmentation_confirmed) {
    warnings.push('The two segmentation methods split the exercises differently — review and confirm the right split below.');
  }
  // [v8.1] Garbled formulas were transcribed by the vision model — the ONLY
  // place model-generated text enters a statement, so the teacher reviews it.
  if (report.vlm_repairs?.length) {
    const n = report.vlm_repairs.filter((r) => r.status === 'replaced').length;
    const issues = report.vlm_repairs.length - n;
    if (n) warnings.push(`${n} garbled formula region(s) transcribed by the vision model — verify them against the PDF.`);
    if (issues) warnings.push(`${issues} garbled region(s) could not be repaired (unreadable / refused / failed) — original text kept.`);
  }
  return warnings;
}
