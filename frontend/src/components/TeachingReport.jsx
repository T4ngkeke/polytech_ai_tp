/**
 * TeachingReport.jsx — a report the model writes for the teacher, on a lab or
 * on one student of it (studentId). Written only when the teacher asks; the
 * latest one is shown with what it is based on.
 */
import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';

import api from '../lib/api';
import { POLL_MS, basis, failureText } from '../lib/reports';

const MessageContent = lazy(() => import('./MessageContent'));

export default function TeachingReport({ labId, studentId = null, studentName = null }) {
  const [report, setReport] = useState(undefined);       // undefined: loading; null: none yet
  const [starting, setStarting] = useState(false);
  const query = studentId ? `?student_id=${studentId}` : '';
  const url = `/api/teacher/labs/${labId}/reports`;

  const load = useCallback(() => api.get(`${url}${query}`).then((r) => setReport(r.report))
    .catch(() => setReport(null)), [url, query]);
  useEffect(() => { setReport(undefined); load(); }, [load]);

  const running = report?.status === 'running';
  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [running, load]);

  const write = async () => {
    setStarting(true);
    try {
      setReport(await api.post(url, studentId ? { student_id: studentId } : {}));
    } catch (err) {
      toast.error(err.message);
    } finally {
      setStarting(false);
    }
  };

  const subject = studentName ? `on ${studentName}` : 'on this lab';
  const failed = failureText(report);
  return (
    <section className="rounded-xl border border-border-subtle bg-ink-raised p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h4 className="text-sm font-semibold text-cream">AI report {subject}</h4>
          <p className="text-[11px] text-cream-muted">
            {studentName
              ? 'How this student asks, topics, what seems understood or hard — from the questions only.'
              : 'Topics, most asked PDFs and pages, difficulties, how to prepare the next session.'}
          </p>
        </div>
        <button type="button" disabled={starting || running || report === undefined} onClick={write}
          className="shrink-0 rounded-lg border border-cyan/40 bg-cyan-muted px-3 py-1.5 text-xs text-cream hover:bg-cyan/20 disabled:opacity-50">
          {running ? 'Writing…' : report ? 'Write again' : 'Write report'}
        </button>
      </div>

      {running && (
        <p className="mt-3 flex items-center gap-2 text-xs text-cream-muted">
          <span className="h-3.5 w-3.5 rounded-full border-2 border-cyan border-t-transparent animate-[spin_0.8s_linear_infinite]" />
          The model is writing the report (about half a minute). You can leave this page.
        </p>
      )}
      {failed && <p className="mt-3 text-xs text-gold">{failed}</p>}
      {report?.status === 'done' && (
        <div className="mt-3 border-t border-border-subtle pt-3">
          <p className="mb-2 text-[11px] text-cream-muted">
            Written {new Date(report.finished_at || report.created_at).toLocaleString()} · {basis(report)}.
            {' '}Written by a model from the questions: check it before you rely on it.
          </p>
          <div className="text-sm text-cream-secondary">
            <Suspense fallback={<pre className="whitespace-pre-wrap text-xs">{report.content}</pre>}>
              <MessageContent content={report.content} />
            </Suspense>
          </div>
        </div>
      )}
    </section>
  );
}
