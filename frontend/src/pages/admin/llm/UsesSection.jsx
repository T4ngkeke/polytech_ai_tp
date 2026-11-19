/**
 * admin/llm/UsesSection.jsx — "What each feature uses": every feature that
 * calls a model, the server it calls, and the switches for image views and
 * reranking. Student answers and image views can move to another chat server.
 */
import { useState } from 'react';
import Icon from '../../../components/Icon';
import { Card, Note, Switch } from './parts';

export default function UsesSection({ st, set }) {
  const [choose, setChoose] = useState(false);
  const name = (id) => st.servers.find((s) => s.id === id)?.name || '—';
  const chats = st.servers.filter((s) => s.kind === 'chat');
  const serverOf = { embed: 'embed', rerank: 'rerank', pdf: 'pdf' };
  const rows = [
    { what: 'Student answers', sub: 'Direct, Search and Research', use: 'answers' },
    { what: 'Answer language, chat titles, rule check, AI reports', sub: 'Always the same server as student answers', use: 'answers', follows: true },
    { what: 'Image views', sub: 'Research looks at figures; teachers ask for figure descriptions; picture captions at upload', use: 'vision', toggle: 'vision' },
    { what: 'Search: embeddings', sub: 'Turns questions and course passages into vectors', fixed: 'embed' },
    { what: 'Search: reranking', sub: 'Orders the passages found. Off: search order only', fixed: 'rerank', toggle: 'rerank' },
    { what: 'PDF reading', sub: 'Reads each page of an uploaded PDF', fixed: 'pdf' },
  ];
  const toggle = (k, label) => <Switch on={st.on[k]} label={label} onChange={(v) => set((p) => ({ ...p, on: { ...p.on, [k]: v } }))} />;
  return (
    <Card id="llm-uses" title="What each feature uses" text="Every feature that calls a model, and the server it calls.">
      <div className="divide-y divide-border-subtle">
        {rows.map((r) => {
          const off = r.toggle && !st.on[r.toggle];
          const target = r.fixed ? serverOf[r.fixed] : st.use[r.use];
          return (
            <div key={r.what} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 py-2.5 md:grid-cols-[minmax(0,1fr)_44px_auto]">
              <div className="min-w-0">
                <p className="font-medium text-cream">{r.what}</p>
                <p className="text-xs text-cream-muted">{r.sub}</p>
              </div>
              <div className="flex justify-end md:justify-center">{r.toggle ? toggle(r.toggle, r.what) : null}</div>
              <div className="col-span-2 flex items-center gap-2 md:col-span-1 md:justify-end">
                <span className="hidden text-cream-faint md:inline">→</span>
                {off ? <span className="rounded-full bg-ink-surface px-2.5 py-0.5 text-xs text-cream-muted">off</span>
                  : choose && r.use && !r.follows && chats.length > 1 ? (
                    <select aria-label={`Server for ${r.what}`} value={st.use[r.use]}
                      onChange={(e) => set((p) => ({ ...p, use: { ...p.use, [r.use]: e.target.value } }))}
                      className="rounded-lg border border-border-default bg-ink-deep px-2.5 py-1.5 text-sm text-cream focus:outline-none focus:border-cyan/50">
                      {chats.map((s) => <option key={s.id} value={s.id}>{s.name || 'New server'}</option>)}
                    </select>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-ink-surface px-2.5 py-0.5 text-xs text-cream-secondary">
                      <Icon name="server" className="h-3 w-3" />{name(target)}
                    </span>
                  )}
              </div>
            </div>
          );
        })}
      </div>
      {!st.on.vision && <Note tone="warn">With image views off, Research cannot look at figures, teachers cannot ask for figure descriptions, and new PDFs get no picture captions.</Note>}
      {!st.on.rerank && <Note tone="warn">With reranking off, Search no longer says “not in the course” on its own: that check uses the reranker’s score.</Note>}
      <div>
        <button type="button" aria-expanded={choose} onClick={() => setChoose(!choose)} disabled={chats.length < 2 && !choose}
          className="inline-flex items-center gap-1.5 py-1 text-sm text-cyan cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed">
          <Icon name="chevronRight" className={`h-3.5 w-3.5 transition-transform ${choose ? '-rotate-90' : 'rotate-90'}`} />
          {choose ? 'Done choosing' : 'Choose another server for a feature'}
        </button>
        {chats.length < 2 && <span className="ml-2 text-xs text-cream-muted">Add a second chat model above first.</span>}
      </div>
    </Card>
  );
}
