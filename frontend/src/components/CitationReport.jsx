/**
 * CitationReport.jsx — the "Course use" view of a lab's audit: which files,
 * pages and passages the research answers cite, who asked what, the files no
 * answer cited, and the questions without a verified answer. Numbers come from
 * GET /api/teacher/labs/{labId}/citation-report; nothing here is computed by a model.
 */
import { useEffect, useState } from 'react';
import api from '../lib/api';
import { reportMarkdown } from '../lib/citationReport';

const day = (iso) => (iso ? new Date(iso).toLocaleDateString() : '');
const count = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

function Question({ item, onOpen }) {
  return (
    <li className="text-xs text-cream-secondary">
      <span className="font-medium text-cream">{item.student}</span>
      <span className="text-cream-muted"> · {day(item.asked_at)} · </span>
      {item.question || <span className="italic text-cream-muted">question not stored</span>}
      {item.session_id && (
        <button type="button" onClick={() => onOpen(item.student_id, item.session_id)}
          className="ml-2 text-cyan hover:underline cursor-pointer">open conversation</button>
      )}
    </li>
  );
}

function FileRow({ file, onOpen }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="bg-ink-raised rounded-xl border border-border-subtle overflow-hidden">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
        className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left cursor-pointer hover:bg-ink-hover transition-colors">
        <div className="min-w-0">
          <p className="text-sm font-medium text-cream truncate">{file.filename}
            {!file.in_lab && <span className="ml-2 text-[10px] text-cream-muted">(no longer in the lab)</span>}</p>
          <p className="text-xs text-cream-muted font-mono">
            {count(file.answers, 'answer')} · {Math.round(file.share * 100)}% · {count(file.students, 'student')} · {count(file.pages.length, 'page')}
          </p>
        </div>
        <span className={`text-cream-muted transition-transform ${open ? 'rotate-90' : ''}`}>›</span>
      </button>
      {open && (
        <div className="border-t border-border-subtle bg-ink-base divide-y divide-border-subtle">
          {file.pages.map((page) => (
            <div key={page.pdf_page_no} className="px-4 py-3 space-y-2">
              <p className="text-xs font-semibold text-cream">
                Page {page.pdf_page_no}
                <span className="ml-2 font-normal text-cream-muted">{count(page.answers, 'answer')} · {count(page.students, 'student')}</span>
              </p>
              {page.passages.some((p) => p.excerpt) && (
                <ul className="space-y-1">
                  {page.passages.filter((p) => p.excerpt).slice(0, 3).map((p) => (
                    <li key={p.block_id} className="text-xs text-cream-muted">
                      “{p.excerpt}” <span className="font-mono">({p.answers})</span>
                    </li>
                  ))}
                </ul>
              )}
              <ul className="space-y-1">
                {page.examples.map((e) => <Question key={e.message_id} item={e} onOpen={onOpen} />)}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function CitationReport({ labId, labName, onOpenConversation }) {
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let current = true;
    const params = new URLSearchParams();
    if (start) params.set('start', start);
    if (end) params.set('end', end);
    setError(null);
    api.get(`/api/teacher/labs/${labId}/citation-report${params.toString() ? `?${params}` : ''}`)
      .then((r) => { if (current) setReport(r); })
      .catch((err) => { if (current) { setReport(null); setError(err.message || 'Could not load the report'); } });
    return () => { current = false; };
  }, [labId, start, end]);

  const t = report?.totals;
  return (
    <div className="space-y-5 max-w-3xl">
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-cream-muted">From
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)}
            className="ml-2 px-2 py-1 rounded-lg bg-ink-deep border border-border-default text-cream text-xs" />
        </label>
        <label className="text-xs text-cream-muted">To
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)}
            className="ml-2 px-2 py-1 rounded-lg bg-ink-deep border border-border-default text-cream text-xs" />
        </label>
        <button type="button" disabled={!report}
          onClick={() => download(`course-use-${labName || labId}.md`, reportMarkdown(report, labName))}
          className="ml-auto px-3 py-1.5 rounded-lg border border-border-default text-cream-secondary text-xs hover:bg-ink-hover disabled:opacity-40 cursor-pointer">
          Export Markdown
        </button>
      </div>
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      {!report && !error && <p className="text-sm text-cream-muted">Loading…</p>}
      {report && (
        <>
          <p className="text-xs text-cream-muted">
            {count(t.questions, 'question')} from {count(t.students, 'student')} · {count(t.answers_with_sources, 'answer')} citing
            the course · {t.questions_back ?? 0} answered with a question back · {t.unverified} without a verified
            answer · {t.marked_wrong} marked wrong. Teacher test drives are not counted.
          </p>
          <section className="space-y-2">
            <h4 className="text-xs font-semibold uppercase tracking-widest text-cream-muted">Cited files</h4>
            {report.files.length === 0
              ? <p className="text-sm text-cream-muted italic">No answer cited the course in this period.</p>
              : report.files.map((f) => <FileRow key={f.document_id} file={f} onOpen={onOpenConversation} />)}
          </section>
          {report.uncited_files.length > 0 && (
            <section className="space-y-2">
              <h4 className="text-xs font-semibold uppercase tracking-widest text-cream-muted">Files no answer cited</h4>
              <p className="text-xs text-cream-muted">Not asked about, or not found by search: worth a check in Documents.</p>
              <ul className="text-sm text-cream-secondary list-disc pl-5">
                {report.uncited_files.map((f) => <li key={f.document_id}>{f.filename}</li>)}
              </ul>
            </section>
          )}
          {report.unverified.length > 0 && (
            <section className="space-y-2">
              <h4 className="text-xs font-semibold uppercase tracking-widest text-cream-muted">Questions without a verified answer</h4>
              <ul className="space-y-1">
                {report.unverified.map((q) => (
                  <Question key={`${q.session_id}-${q.asked_at}`} item={q} onOpen={onOpenConversation} />
                ))}
              </ul>
            </section>
          )}
          {report.marked_wrong.length > 0 && (
            <section className="space-y-2">
              <h4 className="text-xs font-semibold uppercase tracking-widest text-cream-muted">Answers marked wrong by a student</h4>
              <ul className="space-y-1">
                {report.marked_wrong.map((w) => (
                  <Question key={w.message_id} item={{ ...w, question: `${w.question || ''} — cited ${
                    w.pages.map((p) => `${p.filename} p. ${p.pdf_page_no}`).join(', ')}` }} onOpen={onOpenConversation} />
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}
