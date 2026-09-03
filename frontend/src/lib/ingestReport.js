// ingestReport.js — turn a document's ingest_report into teacher warnings.
//
// The report shape is now single:
//   { chunk_count, ingest_tokens_est, token_budget, over_budget, vlm_repairs? }
//
// It used to have a second, exercise-shaped half — numbering anomalies, missing
// numbers, in-lab collisions, which segmenter ran, whether the two segmenters
// disagreed. Those described a document being cut into Exercises rows, which no
// longer happens, so the keys are ignored even if a stale backend still sends
// them.
//
// Kept in one place so the frontend contract can't silently drift from the
// worker's report keys (as it did when `resegmented` was renamed to `segmenter`).

/**
 * @param {object|null|undefined} report
 * @returns {string[]} human-readable warnings (empty when the ingest looks clean)
 */
export function ingestReportWarnings(report) {
  if (!report) return [];
  const warnings = [];

  if (report.over_budget) {
    warnings.push(`Ingest token budget exceeded (~${report.ingest_tokens_est} est. > ${report.token_budget})`);
  }
  // Garbled formulas transcribed by the vision model — the ONLY place
  // model-generated text enters the indexed material, so the teacher reviews it.
  if (report.vlm_repairs?.length) {
    const repaired = report.vlm_repairs.filter((r) => r.status === 'replaced').length;
    const issues = report.vlm_repairs.length - repaired;
    if (repaired) {
      warnings.push(`${repaired} garbled formula region(s) transcribed by the vision model — verify them against the PDF.`);
    }
    if (issues) {
      warnings.push(`${issues} garbled region(s) could not be repaired (unreadable / refused / failed) — original text kept.`);
    }
  }
  return warnings;
}
