/**
 * teacher/QuestionSearch.jsx — Audit › search the students' questions of a lab
 * (GET /api/teacher/labs/{id}/search?q=): who asked, when, how it was answered
 * (cites the course or not); a row opens the conversation in "By student".
 */
import { useEffect, useState } from 'react';
import api from '../../lib/api';
import { useLanguage, useT } from '../../lib/i18n';
import { relativeTime } from '../student/Notifications';

function Highlighted({ text, term }) {
  const at = term ? text.toLowerCase().indexOf(term.toLowerCase()) : -1;
  if (at === -1) return text;
  return <>{text.slice(0, at)}<mark className="rounded bg-hl/40 px-0.5 text-cream">{text.slice(at, at + term.length)}</mark>{text.slice(at + term.length)}</>;
}

export default function QuestionSearch({ labId, onOpenConversation }) {
  const t = useT();
  const lang = useLanguage();
  const [q, setQ] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) { setResult(null); return undefined; }
    setLoading(true);
    const id = setTimeout(() => {
      api.get(`/api/teacher/labs/${labId}/search?q=${encodeURIComponent(term)}`).then(setResult)
        .catch(() => setResult({ total: 0, students: 0, items: [] })).finally(() => setLoading(false));
    }, 250);
    return () => clearTimeout(id);
  }, [q, labId]);
  return (
    <div className="max-w-3xl">
      <input id="audit-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('audit.searchPlaceholder')}
        aria-label={t('audit.searchPlaceholder')} autoFocus
        className="w-full rounded-xl border border-border-default bg-ink-raised px-3.5 py-2.5 text-[14px] text-cream outline-none focus:border-cream-muted" />
      {loading && <p className="mt-3 text-[13px] text-cream-muted">…</p>}
      {result && !loading && (
        <>
          <p className="mt-3 text-[13px] text-cream-secondary">{t('audit.searchSummary', { n: result.total, s: result.students })}</p>
          <div className="mt-2 divide-y divide-border-subtle">
            {result.items.map((item) => (
              <button key={item.message_id} type="button" onClick={() => onOpenConversation(item.student_id, item.session_id)}
                className="block w-full px-1 py-3 text-left hover:bg-ink-hover">
                <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-cream-muted">
                  <span className="font-medium text-cream-secondary">{item.student_name}</span>
                  <span>{relativeTime(lang, item.created_at)}</span>
                  {item.cited
                    ? <span className="rounded bg-cyan/10 px-1.5 text-cyan">{t('audit.cited')}</span>
                    : item.answer_mode && <span className="rounded bg-warn-soft px-1.5 text-gold">{t('audit.notCited')}</span>}
                  {item.session_deleted && <span className="rounded bg-ink-surface px-1.5">{t('audit.deleted')}</span>}
                </div>
                <p className="mt-1 line-clamp-3 text-[14px] text-cream"><Highlighted text={item.question} term={q.trim()} /></p>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
