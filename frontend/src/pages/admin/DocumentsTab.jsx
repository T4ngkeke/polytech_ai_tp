/**
 * admin/DocumentsTab.jsx — Admin "Documents" tab: all uploaded documents and their state.
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import Icon from '../../components/Icon';
import { displayStatus, statusGroup } from '../../lib/documentProgress';
import { PageHeader, Spinner, StatCard } from './shared';

/* ═══════════════════════════════ DOCUMENTS TAB ═══════════════════════════════ */
// Did each upload work? Every course document of every class, newest first.
const DOC_FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'problem', label: 'Problems' },
  { key: 'working', label: 'Being read' },
  { key: 'ready', label: 'Ready' },
];
const DOC_STATUS = {
  pending: ['waiting', 'bg-ink-surface text-cream-muted'],
  processing: ['reading', 'bg-cyan-muted text-cyan'],
  parsed: ['indexing', 'bg-cyan-muted text-cyan'],
  indexed: ['ready', 'bg-success-muted text-success'],
  partially_indexed: ['some pages not read', 'bg-gold-muted text-gold'],
  needs_review: ['needs review', 'bg-gold-muted text-gold'],
  failed: ['failed', 'bg-danger-muted text-danger'],
};
// Why a document has a problem, when the server gave no message.
const DOC_PROBLEM = {
  failed: 'Processing failed.',
  needs_review: 'Some pages could not be read: the teacher must check them and publish.',
  partially_indexed: 'Some pages could not be read: students cannot find their content.',
};

function uploadedAt(iso) {
  if (!iso) return 'before the PDF path';
  return new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

export default function DocumentsTab() {
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
        <PageHeader title="Documents" subtitle="Every course document uploaded by teachers, newest first, and how its upload went." />
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
          <StatCard label="Documents" value={docs.length} />
          <StatCard label="Ready" value={counts.ready} />
          <StatCard label="Being read" value={counts.working} />
          <StatCard label="Problems" value={counts.problem} />
        </div>

        <div className="flex flex-wrap items-center gap-3 mb-4">
          <div className="relative flex-1 min-w-[220px]">
            <Icon name="search" className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-cream-muted" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search file, class, lab or teacher…"
              className="w-full pl-9 pr-3 py-2 rounded-lg bg-ink-raised border border-border-default text-cream text-sm placeholder:text-cream-muted focus:outline-none focus:border-cyan/40 transition-colors" />
          </div>
          <div className="flex flex-wrap items-center gap-1 p-1 rounded-lg bg-ink-raised border border-border-subtle">
            {DOC_FILTERS.map(({ key, label }) => (
              <button key={key} onClick={() => setFilter(key)}
                className={`px-3 py-1 rounded-md text-xs font-medium transition-colors cursor-pointer ${
                  filter === key ? 'bg-cyan-muted text-cyan' : 'text-cream-secondary hover:text-cream'
                }`}>
                {label}{key !== 'all' && counts[key] ? ` (${counts[key]})` : ''}
              </button>
            ))}
          </div>
        </div>

        <div className="rounded-xl border border-border-subtle bg-ink-raised overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead>
              <tr className="bg-ink-surface/40 border-b border-border-subtle text-[10px] uppercase tracking-widest text-cream-muted">
                <th className="text-left font-medium px-4 py-3">Document</th>
                <th className="text-left font-medium px-4 py-3">Class / lab</th>
                <th className="text-left font-medium px-4 py-3">Uploaded</th>
                <th className="text-left font-medium px-4 py-3">Status</th>
                <th className="text-right font-medium px-4 py-3">Pages</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((d) => {
                const [label, cls] = DOC_STATUS[d.shown] || [d.shown, 'bg-ink-surface text-cream-muted'];
                const problem = statusGroup(d.shown) === 'problem'
                  && (d.error_message || d.latest_revision?.error_code || DOC_PROBLEM[d.shown]);
                return (
                  <tr key={d.id} className="border-b border-border-subtle last:border-0 hover:bg-ink-hover/40 transition-colors align-top">
                    <td className="px-4 py-2.5 max-w-[280px]">
                      <p className="font-medium text-cream truncate" title={d.filename}>{d.filename}</p>
                      {problem && <p className="text-[11px] text-danger break-words">{problem}</p>}
                    </td>
                    <td className="px-4 py-2.5 max-w-[220px]">
                      <p className="text-cream-secondary truncate" title={d.class_name}>{d.class_name}</p>
                      <p className="text-[11px] text-cream-muted truncate">{d.lab_name || 'whole class'}</p>
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap">
                      <p className="text-cream-secondary">{uploadedAt(d.uploaded_at)}</p>
                      <p className="text-[11px] text-cream-muted">{d.uploaded_by || 'unknown'}</p>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={`rounded px-2 py-0.5 text-xs font-medium whitespace-nowrap ${cls}`}>{label}</span>
                    </td>
                    <td className="px-4 py-2.5 text-right text-cream-secondary">{d.page_count ?? '–'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {visible.length === 0 && (
            <p className="text-center text-sm text-cream-muted py-10">
              {docs.length === 0 ? 'No documents uploaded yet.' : 'No documents match your filters.'}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
