/** Why a cited source cannot be shown (lib/sourcePdf.js, the viewers). */

// The server closes course documents to students in exam mode (routers/document_reader.py).
export const EXAM_CLOSED = 'Exam mode: your teacher has closed the course documents for now. They open again when exam mode is turned off.';

/** The message a viewer shows when a source cannot be loaded. */
export function sourceError(err, notFound, fallback) {
  if (err?.status === 403 && err.message === 'exam_mode') return EXAM_CLOSED;
  if (err?.status === 404) return notFound;
  return fallback ?? (err?.message || notFound);
}
