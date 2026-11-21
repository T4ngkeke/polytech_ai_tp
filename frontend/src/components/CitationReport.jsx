/**
 * CitationReport.jsx — the "Course use" view of a lab's audit, or of a whole
 * class (classId): which files, pages and passages the research answers cite,
 * who asked what, the files no answer cited, and the questions without a
 * verified answer. Numbers come from GET /api/teacher/{labs|classes}/{id}/citation-report;
 * nothing here is computed by a model. Exported as Markdown or printed (PDF).
 */
import { useEffect, useRef, useState } from 'react';
import api from '../lib/api';
import { labOf, reportMarkdown } from '../lib/citationReport';
import { downloadText, printElement } from '../lib/exportFile';
import { useT } from '../lib/i18n';

const day = (iso) => (iso ? new Date(iso).toLocaleDateString() : '');

// "3 answers" in the page language; word is answer, question, student or page.
function useCount() {
  const t = useT();
  return (n, word) => t(n === 1 ? `p.cr.${word}1` : `p.cr.${word}N`, { n });
}

// The period of the report in words (the API end is exclusive: the day after the last day chosen).
function periodWords(period, t) {
  const iso = (v) => (v ? v.slice(0, 10) : '');
  const start = iso(period?.start);
  const end = period?.end ? iso(new Date(new Date(period.end).getTime() - 86400000).toISOString()) : '';
  if (start && end) return t('p.cr.periodRange', { a: start, b: end });
  if (start) return t('p.cr.periodFrom', { a: start });
  if (end) return t('p.cr.periodUntil', { a: end });
  return t('p.cr.periodAll');
}

function Question({ item, lab, onOpen }) {
  const t = useT();
  return (
    <li className="text-[13px] text-cream-secondary">
      <span className="font-medium text-cream">{item.student}</span>
      {lab && <span className="text-cream-muted">{lab}</span>}
      <span className="text-cream-muted"> · {day(item.asked_at)} · </span>
      {item.question || <span className="italic text-cream-muted">{t('p.cr.notStored')}</span>}
      {item.session_id && (
        <button type="button" onClick={() => onOpen(item.student_id, item.session_id, item.lab_id)}
          className="no-print ml-2 cursor-pointer text-cream-secondary underline hover:text-cream">{t('p.cr.open')}</button>
      )}
    </li>
  );
}

function FileRow({ file, report, scopeWord, printing, onOpen }) {
  const t = useT();
  const count = useCount();
  const [opened, setOpen] = useState(false);
  // A printed report shows every file open.
  const open = opened || printing;
  return (
    <div className="overflow-hidden rounded-2xl border border-border-subtle bg-ink-raised">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
        className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left cursor-pointer hover:bg-ink-hover transition-colors">
        <div className="min-w-0">
          <p className="truncate text-[14px] font-medium text-cream">{file.filename}
            {!file.in_lab && <span className="ml-2 text-[12px] text-cream-muted">{t(scopeWord === 'class' ? 'p.cr.notInClass' : 'p.cr.notInLab')}</span>}</p>
          <p className="text-[13px] text-cream-muted">
            {count(file.answers, 'answer')} · {Math.round(file.share * 100)}% · {count(file.students, 'student')} · {count(file.pages.length, 'page')}
          </p>
        </div>
        <span className={`text-cream-muted transition-transform ${open ? 'rotate-90' : ''}`}>›</span>
      </button>
      {open && (
        <div className="border-t border-border-subtle bg-ink-base divide-y divide-border-subtle">
          {file.pages.map((page) => (
            <div key={page.pdf_page_no} className="px-4 py-3 space-y-2">
              <p className="text-[13px] font-semibold text-cream">
                {t('p.cr.page', { n: page.pdf_page_no })}
                <span className="ml-2 font-normal text-cream-muted">{count(page.answers, 'answer')} · {count(page.students, 'student')}</span>
              </p>
              {page.passages.some((p) => p.excerpt) && (
                <ul className="space-y-1">
                  {page.passages.filter((p) => p.excerpt).slice(0, 3).map((p) => (
                    <li key={p.block_id} className="text-[13px] text-cream-muted">
                      “{p.excerpt}” <span>({p.answers})</span>
                    </li>
                  ))}
                </ul>
              )}
              <ul className="space-y-1">
                {page.examples.map((e) => <Question key={e.message_id} item={e} lab={labOf(report, e)} onOpen={onOpen} />)}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** labId, or classId for all labs of the class; name is the lab's or the class's. */
export default function CitationReport({ labId, classId, name, onOpenConversation }) {
  const t = useT();
  const count = useCount();
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);
  const [printing, setPrinting] = useState(false);
  const sheet = useRef(null);
  const url = classId ? `/api/teacher/classes/${classId}/citation-report` : `/api/teacher/labs/${labId}/citation-report`;
  const scopeWord = classId ? 'class' : 'lab';

  // Printed once every file is rendered open (FileRow), then back to normal.
  useEffect(() => {
    if (!printing) return;
    printElement(sheet.current, `${t('p.cr.title')} - ${name || ''}`);
    setPrinting(false);
  }, [printing, name, t]);

  useEffect(() => {
    let current = true;
    const params = new URLSearchParams();
    if (start) params.set('start', start);
    if (end) params.set('end', end);
    setError(null);
    api.get(`${url}${params.toString() ? `?${params}` : ''}`)
      .then((r) => { if (current) setReport(r); })
      .catch((err) => { if (current) { setReport(null); setError(err.message || t('p.cr.loadFailed')); } });
    return () => { current = false; };
  }, [url, start, end, t]);

  const totals = report?.totals;
  return (
    <div ref={sheet} className="max-w-3xl space-y-5">
      <h3 className="hidden text-lg font-semibold print:block">{t('p.cr.title')}{name ? ` — ${name}` : ''}</h3>
      {report && <p className="hidden text-xs print:block">{t('p.cr.period', { text: periodWords(report.period, t) })}</p>}
      <div className="no-print flex flex-wrap items-end gap-3">
        <label className="text-[13px] text-cream-muted">{t('p.cr.from')}
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)}
            className="ml-2 rounded-xl border border-border-default bg-ink-raised px-2 py-1 text-[13px] text-cream" />
        </label>
        <label className="text-[13px] text-cream-muted">{t('p.cr.to')}
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)}
            className="ml-2 rounded-xl border border-border-default bg-ink-raised px-2 py-1 text-[13px] text-cream" />
        </label>
        <button type="button" disabled={!report}
          onClick={() => downloadText(`course-use-${name || labId || classId}.md`, reportMarkdown(report, name))}
          className="ml-auto cursor-pointer rounded-full border border-border-default px-3 py-1.5 text-[13px] text-cream-secondary hover:bg-ink-hover disabled:opacity-40">
          {t('p.exportMd')}
        </button>
        <button type="button" disabled={!report} onClick={() => setPrinting(true)}
          title={t('p.printTitle')}
          className="cursor-pointer rounded-full border border-border-default px-3 py-1.5 text-[13px] text-cream-secondary hover:bg-ink-hover disabled:opacity-40">
          {t('p.print')}
        </button>
      </div>
      {error && <p role="alert" className="text-[14px] text-danger">{error}</p>}
      {!report && !error && <p className="text-[14px] text-cream-muted">{t('p.loading')}</p>}
      {report && (
        <>
          <p className="text-[13px] text-cream-muted">
            {t('p.cr.summary', { q: count(totals.questions, 'question'), s: count(totals.students, 'student'),
              a: count(totals.answers_with_sources, 'answer'), back: totals.questions_back ?? 0,
              unverified: totals.unverified, wrong: totals.marked_wrong })}
          </p>
          <section className="space-y-2">
            <h4 className="text-[15px] font-semibold text-cream">{t('p.cr.cited')}</h4>
            {report.files.length === 0
              ? <p className="text-[14px] text-cream-muted">{t('p.cr.noCited')}</p>
              : report.files.map((f) => <FileRow key={f.document_id} file={f} report={report} scopeWord={scopeWord}
                  printing={printing} onOpen={onOpenConversation} />)}
          </section>
          {report.uncited_files.length > 0 && (
            <section className="space-y-2">
              <h4 className="text-[15px] font-semibold text-cream">{t('p.cr.uncited')}</h4>
              <p className="text-[13px] text-cream-muted">{t('p.cr.uncitedNote')}</p>
              <ul className="list-disc pl-5 text-[14px] text-cream-secondary">
                {report.uncited_files.map((f) => <li key={f.document_id}>{f.filename}</li>)}
              </ul>
            </section>
          )}
          {report.unverified.length > 0 && (
            <section className="space-y-2">
              <h4 className="text-[15px] font-semibold text-cream">{t('p.cr.unverified')}</h4>
              <ul className="space-y-1">
                {report.unverified.map((q) => (
                  <Question key={`${q.session_id}-${q.asked_at}`} item={q} lab={labOf(report, q)} onOpen={onOpenConversation} />
                ))}
              </ul>
            </section>
          )}
          {report.marked_wrong.length > 0 && (
            <section className="space-y-2">
              <h4 className="text-[15px] font-semibold text-cream">{t('p.cr.wrong')}</h4>
              <ul className="space-y-1">
                {report.marked_wrong.map((w) => (
                  <Question key={w.message_id} item={{ ...w, question: `${w.question || ''} — ${t('p.cr.cited1', {
                    pages: w.pages.map((pg) => `${pg.filename} p. ${pg.pdf_page_no}`).join(', ') })}` }} lab={labOf(report, w)}
                    onOpen={onOpenConversation} />
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}
