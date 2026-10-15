/**
 * Teacher reports written by the model (components/TeachingReport.jsx).
 * API: POST/GET /api/teacher/labs/{labId}/reports[?student_id=]
 */

export const POLL_MS = 3000;

/** Why there is no text, in words a teacher understands; null when there is text. */
export function failureText(report) {
  if (!report || report.status !== 'failed') return null;
  switch (report.error) {
    case 'no_questions': return 'No questions yet: there is nothing to report on.';
    case 'interrupted': return 'The report was interrupted (the server restarted). Write it again.';
    case 'empty_reply': return 'The model returned an empty text. Write it again.';
    default: return `The model could not write the report (${report.error || 'unknown error'}). Write it again later.`;
  }
}

/** "Based on 108 questions from 14 Oct to 15 Oct · 24 matched to pages by search". */
export function basis(report) {
  const f = report?.facts;
  if (!f) return '';
  const day = (iso) => new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
  const parts = [`Based on ${f.questions} question${f.questions === 1 ? '' : 's'}`];
  if (f.period?.first_question) {
    const from = day(f.period.first_question);
    const to = day(f.period.last_question);
    parts[0] += from === to ? ` on ${from}` : ` from ${from} to ${to}`;
  }
  if (f.questions_given_to_model < f.questions) parts.push(`the latest ${f.questions_given_to_model} given to the model`);
  if (f.pages_matched_by_search) parts.push(`${f.pages_matched_by_search} tied to pages by a course search`);
  return parts.join(' · ');
}
