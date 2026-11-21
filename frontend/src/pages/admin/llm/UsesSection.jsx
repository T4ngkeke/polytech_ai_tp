/**
 * admin/llm/UsesSection.jsx — "What each feature uses": every feature that
 * calls a model, the server it calls, and the switches for image views and
 * reranking. Student answers and image views can move to another chat server.
 */
import { useState } from 'react';
import Icon from '../../../components/Icon';
import { useT } from '../../../lib/i18n';
import { Card, Note, Switch } from './parts';

export default function UsesSection({ st, set }) {
  const t = useT();
  const [choose, setChoose] = useState(false);
  const name = (id) => st.servers.find((s) => s.id === id)?.name || '—';
  const chats = st.servers.filter((s) => s.kind === 'chat');
  const serverOf = { embed: 'embed', rerank: 'rerank', pdf: 'pdf' };
  const rows = [
    { what: 'answers', use: 'answers' },
    { what: 'helpers', use: 'answers', follows: true },
    { what: 'vision', use: 'vision', toggle: 'vision' },
    { what: 'embed', fixed: 'embed' },
    { what: 'rerank', fixed: 'rerank', toggle: 'rerank' },
    { what: 'pdf', fixed: 'pdf' },
  ];
  const toggle = (k, label) => <Switch on={st.on[k]} label={label} onChange={(v) => set((p) => ({ ...p, on: { ...p.on, [k]: v } }))} />;
  return (
    <Card id="llm-uses" title={t('a.llm.uses')} text={t('a.llm.usesText')}>
      <div className="divide-y divide-border-subtle">
        {rows.map((r) => {
          const off = r.toggle && !st.on[r.toggle];
          const target = r.fixed ? serverOf[r.fixed] : st.use[r.use];
          const what = t(`a.use.${r.what}`);
          return (
            <div key={r.what} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 py-2.5 md:grid-cols-[minmax(0,1fr)_44px_auto]">
              <div className="min-w-0">
                <p className="text-[14px] font-medium text-cream">{what}</p>
                <p className="text-[13px] text-cream-muted">{t(`a.use.${r.what}.sub`)}</p>
              </div>
              <div className="flex justify-end md:justify-center">{r.toggle ? toggle(r.toggle, what) : null}</div>
              <div className="col-span-2 flex items-center gap-2 md:col-span-1 md:justify-end">
                <span className="hidden text-cream-muted md:inline">→</span>
                {off ? <span className="rounded-full bg-ink-hover px-2.5 py-0.5 text-[12.5px] text-cream-muted">{t('a.llm.off')}</span>
                  : choose && r.use && !r.follows && chats.length > 1 ? (
                    <select aria-label={t('a.llm.serverFor', { what })} value={st.use[r.use]}
                      onChange={(e) => set((p) => ({ ...p, use: { ...p.use, [r.use]: e.target.value } }))}
                      className="rounded-xl border border-border-default bg-ink-raised px-2.5 py-1.5 text-[13px] text-cream focus:border-cream-muted focus:outline-none">
                      {chats.map((s) => <option key={s.id} value={s.id}>{s.name || t('a.llm.newServer')}</option>)}
                    </select>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-ink-hover px-2.5 py-0.5 text-[12.5px] text-cream-secondary">
                      <Icon name="server" className="h-3 w-3" />{name(target)}
                    </span>
                  )}
              </div>
            </div>
          );
        })}
      </div>
      {!st.on.vision && <Note tone="warn">{t('a.llm.visionWarn')}</Note>}
      {!st.on.rerank && <Note tone="warn">{t('a.llm.rerankWarn')}</Note>}
      <div>
        <button type="button" aria-expanded={choose} onClick={() => setChoose(!choose)} disabled={chats.length < 2 && !choose}
          className="inline-flex items-center gap-1.5 py-1 text-[13px] text-cream-secondary hover:text-cream cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed">
          <Icon name="chevronRight" className={`h-3.5 w-3.5 transition-transform ${choose ? '-rotate-90' : 'rotate-90'}`} />
          {choose ? t('a.llm.doneChoosing') : t('a.llm.chooseOther')}
        </button>
        {chats.length < 2 && <span className="ml-2 text-[13px] text-cream-muted">{t('a.llm.addFirst')}</span>}
      </div>
    </Card>
  );
}
