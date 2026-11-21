/**
 * TeachingReport.jsx — a report the model writes for the teacher, on a lab or
 * on one student of it (studentId). Written only when the teacher asks; the
 * latest one is shown with what it is based on, and can be exported as
 * Markdown or printed (PDF).
 */
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';

import api from '../lib/api';
import { downloadText, printElement } from '../lib/exportFile';
import { POLL_MS, reportFile } from '../lib/reports';
import { useLanguage, useT } from '../lib/i18n';

const LOCALES = { zh: 'zh-CN', fr: 'fr-FR', en: 'en-GB' };

const MessageContent = lazy(() => import('./MessageContent'));

export default function TeachingReport({ labId, labName = '', studentId = null, studentName = null }) {
  const t = useT();
  const lang = useLanguage();
  const [report, setReport] = useState(undefined);       // undefined: loading; null: none yet
  const sheet = useRef(null);
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

  // The sentences of lib/reports.js (failureText, basis) in the page language.
  const failed = report?.status === 'failed' ? ({
    no_questions: t('p.tr.fail.no_questions'),
    interrupted: t('p.tr.fail.interrupted'),
    empty_reply: t('p.tr.fail.empty_reply'),
  }[report.error] || t('p.tr.fail.other', { error: report.error || t('p.block.unknownError') })) : null;
  const basisText = (() => {
    const f = report?.facts;
    if (!f) return '';
    const dayText = (iso) => new Date(iso).toLocaleDateString(LOCALES[lang] || 'en-GB', { day: 'numeric', month: 'short' });
    let first = t(f.questions === 1 ? 'p.tr.basis1' : 'p.tr.basisN', { n: f.questions });
    if (f.period?.first_question) {
      const from = dayText(f.period.first_question);
      const to = dayText(f.period.last_question);
      first += from === to ? ` ${t('p.tr.on', { a: from })}` : ` ${t('p.tr.fromTo', { a: from, b: to })}`;
    }
    const parts = [first];
    if (f.questions_given_to_model < f.questions) parts.push(t('p.tr.latest', { n: f.questions_given_to_model }));
    if (f.pages_matched_by_search) parts.push(t('p.tr.pagesTied', { n: f.pages_matched_by_search }));
    return parts.join(' · ');
  })();
  const done = report?.status === 'done';
  const file = done ? reportFile(report, { labName, studentName }) : null;
  return (
    <section ref={sheet} className="rounded-2xl border border-border-subtle bg-ink-raised p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h4 className="text-[15px] font-semibold text-cream">{studentName ? t('p.tr.titleStudent', { name: studentName }) : t('p.tr.titleLab')}</h4>
          <p className="text-[13px] text-cream-muted">
            {studentName ? t('p.tr.descStudent') : t('p.tr.descLab')}
          </p>
        </div>
        <div className="no-print flex shrink-0 flex-wrap gap-2">
          {file && <>
            <button type="button" onClick={() => downloadText(file.name, file.markdown)}
              className="cursor-pointer rounded-full border border-border-default px-3 py-1.5 text-[13px] text-cream-secondary hover:bg-ink-hover">
              {t('p.exportMd')}
            </button>
            <button type="button" onClick={() => printElement(sheet.current, file.title)}
              title={t('p.printTitle')}
              className="cursor-pointer rounded-full border border-border-default px-3 py-1.5 text-[13px] text-cream-secondary hover:bg-ink-hover">
              {t('p.print')}
            </button>
          </>}
          <button type="button" disabled={starting || running || report === undefined} onClick={write}
            className="cursor-pointer rounded-full bg-ink px-4 py-1.5 text-[13px] text-on-ink hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50">
            {running ? t('p.tr.writing') : report ? t('p.tr.again') : t('p.tr.write')}
          </button>
        </div>
      </div>

      {running && (
        <p className="mt-3 flex items-center gap-2 text-[13px] text-cream-muted">
          <span className="h-3.5 w-3.5 rounded-full border-2 border-cream-muted border-t-transparent animate-[spin_0.8s_linear_infinite]" />
          {t('p.tr.running')}
        </p>
      )}
      {failed && <p className="mt-3 text-[13px] text-gold">{failed}</p>}
      {done && (
        <div className="mt-3 border-t border-border-subtle pt-3">
          <p className="mb-2 text-[12.5px] text-cream-muted">
            {t('p.tr.written', { when: new Date(report.finished_at || report.created_at).toLocaleString() })}
            {basisText ? ` · ${basisText}` : ''}. {t('p.tr.check')}
          </p>
          <div className="text-[14px] text-cream-secondary">
            <Suspense fallback={<pre className="whitespace-pre-wrap text-xs">{report.content}</pre>}>
              <MessageContent content={report.content} />
            </Suspense>
          </div>
        </div>
      )}
    </section>
  );
}
