/**
 * SearchModal.jsx — search the student's own conversations (Ctrl/Cmd+K):
 * GET /api/student/search?q= (titles and messages, one hit per conversation).
 * Empty: the latest conversations of this lab. Enter or a click opens one.
 */
import { useEffect, useRef, useState } from 'react';
import api from '../../lib/api';
import Icon from '../../components/Icon';
import { useT } from '../../lib/i18n';
import { useDismiss } from './ui';

function Highlighted({ text, term }) {
  if (!term) return text;
  const at = text.toLowerCase().indexOf(term.toLowerCase());
  if (at === -1) return text;
  return <>{text.slice(0, at)}<mark className="rounded bg-hl/40 px-0.5 text-cream">{text.slice(at, at + term.length)}</mark>{text.slice(at + term.length)}</>;
}

export default function SearchModal({ onClose, onOpen }) {
  const t = useT();
  const [q, setQ] = useState('');
  const [hits, setHits] = useState([]);
  const [recent, setRecent] = useState([]);
  const [active, setActive] = useState(0);
  const [loading, setLoading] = useState(false);
  const boxRef = useRef(null);
  useDismiss(boxRef, true, onClose);
  useEffect(() => { api.get('/api/student/sessions').then((list) => setRecent(list.slice(0, 8))).catch(() => {}); }, []);
  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) { setHits([]); return undefined; }
    setLoading(true);
    const id = setTimeout(() => {
      api.get(`/api/student/search?q=${encodeURIComponent(term)}`).then((rows) => { setHits(rows); setActive(0); })
        .catch(() => setHits([])).finally(() => setLoading(false));
    }, 200);
    return () => clearTimeout(id);
  }, [q]);
  const list = q.trim().length >= 2 ? hits : recent.map((s) => ({ session_id: s.id, title: s.title, lab_id: s.lab_id }));
  const open = (hit) => hit && onOpen(hit);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 px-3 pt-[12vh]">
      <div ref={boxRef} role="dialog" aria-modal="true" aria-label={t('search.title')}
        className="w-full max-w-[600px] overflow-hidden rounded-2xl border border-border-subtle bg-ink-raised shadow-elevated">
        <div className="flex items-center gap-2.5 border-b border-border-subtle px-4">
          <Icon name="search" className="h-[18px] w-[18px] text-cream-muted" />
          <input id="search-input" autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('search.placeholder')}
            aria-label={t('search.title')}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(list.length - 1, i + 1)); }
              if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(0, i - 1)); }
              if (e.key === 'Enter') open(list[active]);
            }}
            className="h-12 flex-1 bg-transparent text-[15px] text-cream outline-none placeholder:text-cream-muted" />
          {loading && <span className="h-4 w-4 animate-spin rounded-full border-2 border-cream-faint border-t-cream-secondary" />}
        </div>
        <div className="max-h-[50vh] overflow-y-auto p-1.5">
          {q.trim().length < 2 && <div className="px-3 pb-1 pt-2 text-[12px] font-medium text-cream-muted">{t('search.recent')}</div>}
          {q.trim().length >= 2 && !loading && !hits.length && <p className="px-3 py-6 text-center text-[13.5px] text-cream-muted">{t('search.none')}</p>}
          {list.map((hit, i) => (
            <button key={`${hit.session_id}-${hit.message_id || ''}`} type="button" onClick={() => open(hit)} onMouseEnter={() => setActive(i)}
              className={`block w-full rounded-lg px-3 py-2 text-left ${i === active ? 'bg-ink-hover' : ''}`}>
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-[14px] text-cream"><Highlighted text={hit.title || t('session.untitled')} term={q.trim()} /></span>
                {hit.lab_name && <span className="shrink-0 text-[12px] text-cream-muted">{hit.lab_name}</span>}
              </div>
              {hit.snippet && <p className="mt-0.5 line-clamp-2 text-[13px] text-cream-secondary"><Highlighted text={hit.snippet} term={q.trim()} /></p>}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
