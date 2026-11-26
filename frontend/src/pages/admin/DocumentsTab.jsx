/**
 * admin/DocumentsTab.jsx — Admin "Documents" tab: all uploaded documents and their state.
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import Icon from '../../components/Icon';
import { displayStatus, statusGroup } from '../../lib/documentProgress';
import { useLanguage, useT } from '../../lib/i18n';
import { PageHeader, Spinner, StatCard, inputClass } from './shared';

/* ═══════════════════════════════ DOCUMENTS TAB ═══════════════════════════════ */
// Did each upload work? Every course document of every class, newest first.
const DOC_FILTERS = ['all', 'problem', 'working', 'ready'];
const MUTED_CHIP = 'bg-ink-hover text-cream-muted';
const DOC_STATUS = {
  pending: ['waiting', MUTED_CHIP],
  processing: ['reading', MUTED_CHIP],
  parsed: ['indexing', MUTED_CHIP],
  indexed: ['ready', 'bg-success-muted text-success'],
  partially_indexed: ['partial', 'bg-warn-soft text-gold'],
  needs_review: ['review', 'bg-warn-soft text-gold'],
  to_publish: ['toPublish', 'bg-warn-soft text-gold'],
  failed: ['failed', 'bg-danger-muted text-danger'],
};
const LOCALES = { zh: 'zh-CN', fr: 'fr-FR', en: 'en-GB' };

export default function DocumentsTab() {
  const t = useT();
  const lang = useLanguage();
  const uploadedAt = (iso) => (iso ? new Date(iso).toLocaleString(LOCALES[lang] || 'en-GB',
    { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : t('a.docs.beforePdf'));
  const [docs, setDocs] = useState(null);
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');

  const load = useCallback(() => api.get('/api/admin/documents')
    .then((rows) => setDocs(rows.map((d) => ({ ...d, shown: displayStatus(d) }))))
    .catch((err) => { toast.error(err.message); setDocs((d) => d || []); }), []);
  useEffect(() => { load(); }, [load]);
  // Refresh while something is still being read.
  const working = docs?.some((d) => statusGroup(d.shown) === 'working');
  useEffect(() => {
    if (!working) return;
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [working, load]);

  const counts = useMemo(() => {
    const c = { ready: 0, working: 0, problem: 0 };
    for (const d of docs || []) c[statusGroup(d.shown)] += 1;
    return c;
  }, [docs]);
  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (docs || []).filter((d) => (filter === 'all' || statusGroup(d.shown) === filter)
      && (!q || [d.filename, d.class_name, d.lab_name, d.uploaded_by].some((v) => v?.toLowerCase().includes(q))));
  }, [docs, filter, search]);

  if (!docs) return <Spinner />;

  return (
    <div className="p-4 md:p-8 overflow-y-auto h-full">
      <div className="max-w-5xl mx-auto">
        <PageHeader title={t('a.nav.documents')} subtitle={t('a.docs.subtitle')} />
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
          <StatCard label={t('a.nav.documents')} value={docs.length} />
          <StatCard label={t('a.docs.f.ready')} value={counts.ready} />
          <StatCard label={t('a.docs.f.working')} value={counts.working} />
          <StatCard label={t('a.docs.f.problem')} value={counts.problem} />
        </div>

        <div className="flex flex-wrap items-center gap-3 mb-4">
          <div className="relative flex-1 min-w-[220px]">
            <Icon name="search" className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-cream-muted" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t('a.docs.search')}
              aria-label={t('a.docs.search')} className={`w-full pl-9 ${inputClass}`} />
          </div>
          <div className="flex flex-wrap items-center gap-1 p-0.5 rounded-xl bg-ink-raised border border-border-subtle">
            {DOC_FILTERS.map((key) => (
              <button key={key} type="button" onClick={() => setFilter(key)}
                className={`px-3 py-1 rounded-lg text-[13px] transition-colors cursor-pointer ${
                  filter === key ? 'bg-ink-hover text-cream' : 'text-cream-secondary hover:text-cream'
                }`}>
                {t(`a.docs.f.${key}`)}{key !== 'all' && counts[key] ? ` (${counts[key]})` : ''}
              </button>
            ))}
          </div>
        </div>

        <div className="rounded-2xl border border-border-subtle bg-ink-raised overflow-x-auto">
          <table className="w-full min-w-[640px] text-[14px]">
            <thead>
              <tr className="border-b border-border-subtle text-[12.5px] text-cream-muted">
                <th className="text-left font-normal px-4 py-2.5">{t('a.docs.col.doc')}</th>
                <th className="text-left font-normal px-4 py-2.5">{t('a.docs.col.class')}</th>
                <th className="text-left font-normal px-4 py-2.5">{t('a.docs.col.uploaded')}</th>
                <th className="text-left font-normal px-4 py-2.5">{t('a.docs.col.status')}</th>
                <th className="text-right font-normal px-4 py-2.5">{t('a.docs.col.pages')}</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((d) => {
                const [label, cls] = DOC_STATUS[d.shown] || [null, MUTED_CHIP];
                const problem = statusGroup(d.shown) === 'problem'
                  && (d.error_message || d.latest_revision?.error_code || t(`a.docs.problem.${d.shown}`));
                return (
                  <tr key={d.id} className="border-b border-border-subtle last:border-0 hover:bg-ink-hover/40 transition-colors align-top">
                    <td className="px-4 py-2.5 max-w-[280px]">
                      <p className="font-medium text-cream truncate" title={d.filename}>{d.filename}</p>
                      {problem && <p className="text-[12.5px] text-danger break-words">{problem}</p>}
                    </td>
                    <td className="px-4 py-2.5 max-w-[220px]">
                      <p className="text-cream-secondary truncate" title={d.class_name}>{d.class_name}</p>
                      <p className="text-[12.5px] text-cream-muted truncate">{d.lab_name || t('a.docs.wholeClass')}</p>
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap">
                      <p className="text-cream-secondary">{uploadedAt(d.uploaded_at)}</p>
                      <p className="text-[12.5px] text-cream-muted">{d.uploaded_by || t('a.docs.unknown')}</p>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={`rounded-full px-2.5 py-0.5 text-[12.5px] whitespace-nowrap ${cls}`}>
                        {label ? t(`a.docs.s.${label}`) : d.shown}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-right text-cream-secondary">{d.page_count ?? '–'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {visible.length === 0 && (
            <p className="text-center text-[14px] text-cream-muted py-10">
              {docs.length === 0 ? t('a.docs.none') : t('a.docs.noMatch')}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
