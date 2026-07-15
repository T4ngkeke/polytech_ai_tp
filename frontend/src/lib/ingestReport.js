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
  return warnings;
}
