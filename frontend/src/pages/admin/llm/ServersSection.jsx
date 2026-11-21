/**
 * admin/llm/ServersSection.jsx — "Model servers" of the Model settings page:
 * the shared key, one row per server with its test result, and "Add a server"
 * for a second chat model. State lives in LlmConfigTab.
 */
import { useState } from 'react';
import Icon from '../../../components/Icon';
import { serverUrl, testState, unusedServers, usedFor } from '../../../lib/llmSettings';
import { useT } from '../../../lib/i18n';
import { Card, Switch } from './parts';

const TONE_CHIP = {
  ok: 'bg-success-muted text-success', bad: 'bg-danger-muted text-danger', warn: 'bg-gold-muted text-gold',
  muted: 'bg-ink-hover text-cream-muted', accent: 'bg-ink-hover text-cream',
};

// lib/llmSettings.js words the test result and the uses in English: these map them to our keys.
const USED_KEY = { 'student answers': 'a.use.answers', 'image views': 'a.use.vision', embeddings: 'a.use.embed',
  reranking: 'a.use.rerank', 'PDF reading': 'a.use.pdf' };
const STATUS_KEY = { testing: 'a.st.testing', changed: 'a.st.changed', ok: 'a.st.ok', refused: 'a.st.refused',
  invalid_url: 'a.st.invalidUrl', model_missing: 'a.st.modelMissing' };

function stateText(t, result) {
  const key = result ? STATUS_KEY[result.status] : 'a.st.none';
  if (!result) return t('a.st.none');
  if (!key) return t('a.st.unreachable');
  return t(key, {
    ctx: result.max_model_len ? ` · ${Math.round(result.max_model_len / 1024)}k` : '',
    served: result.served?.length ? result.served.join(', ') : t('a.st.nothing'),
  });
}

const input = 'w-full min-w-0 rounded-xl border border-border-default bg-ink-raised px-3 py-2 text-[14px] text-cream placeholder:text-cream-muted focus:border-cream-muted focus:outline-none';
const button = 'inline-flex items-center gap-1.5 rounded-full border border-border-default px-3.5 py-1.5 text-[13px] text-cream-secondary hover:bg-ink-hover hover:text-cream cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed';

function KeyBox({ st, set }) {
  const t = useT();
  const [show, setShow] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const use = () => { set((p) => ({ ...p, key: draft.trim() })); setEditing(false); setDraft(''); setShow(false); };
  return (
    <div className="flex flex-col gap-3 rounded-xl bg-ink-hover p-3.5 sm:flex-row sm:items-center">
      <Icon name="key" className="hidden h-4 w-4 shrink-0 text-cream-secondary sm:block" />
      <div className="min-w-0 flex-1">
        <p className="text-[13px] text-cream-muted">{t('a.llm.sharedKey')}</p>
        {editing ? (
          <div className="mt-1.5 flex flex-wrap gap-2">
            <input id="llm-key-new" className={`${input} flex-1`} value={draft} autoComplete="off"
              onChange={(e) => setDraft(e.target.value)} placeholder={t('a.llm.pasteKey')} />
            <button type="button" className={`${button} !border-transparent !bg-ink !text-on-ink hover:!opacity-90`} disabled={!draft.trim()} onClick={use}>{t('a.llm.useKey')}</button>
            <button type="button" className={button} onClick={() => { setEditing(false); setDraft(''); }}>{t('cancel')}</button>
          </div>
        ) : (
          <p className="mt-1 break-all text-[13px] text-cream">
            {st.key ? (show ? st.key : `${'•'.repeat(16)} …${st.key.slice(-4)}`) : <span className="text-danger">{t('a.llm.noKey')}</span>}
          </p>
        )}
        <p className="mt-1 text-[13px] leading-relaxed text-cream-muted">{t('a.llm.keyHelp')}</p>
      </div>
      {!editing && (
        <div className="flex gap-2">
          <button type="button" className={button} onClick={() => setShow(!show)}>
            <Icon name={show ? 'eyeOff' : 'eye'} className="h-3.5 w-3.5" />{show ? t('a.hide') : t('a.show')}
          </button>
          <button type="button" className={button} onClick={() => setEditing(true)}>{t('a.llm.change')}</button>
        </div>
      )}
    </div>
  );
}

function ServerRow({ st, set, server: s, result, onTest }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const state = testState(result);
  const used = usedFor(st, s.id);
  const extra = s.kind === 'chat' && s.id !== 'main';
  const edit = (field) => (e) => set((p) => ({ ...p,
    servers: p.servers.map((x) => (x.id === s.id ? { ...x, [field]: e.target.value } : x)) }));
  const setOwn = (on) => set((p) => ({ ...p,
    servers: p.servers.map((x) => (x.id === s.id ? { ...x, ownKey: on } : x)) }));
  const remove = () => set((p) => ({ ...p, servers: p.servers.filter((x) => x.id !== s.id) }));
  const url = serverUrl(s);
  return (
    <div className="border-t border-border-subtle first:border-t-0">
      <div className="grid gap-2 px-3.5 py-3 md:grid-cols-[minmax(150px,1fr)_minmax(0,2fr)_auto] md:items-center md:gap-3">
        <div className="flex items-center gap-2 font-semibold text-cream">
          <Icon name="server" className="h-4 w-4 shrink-0 text-cream-secondary" />{s.name || t('a.llm.newServer')}
        </div>
        <div className="min-w-0">
          <p className="break-all text-[13px] text-cream">{s.model || (s.kind === 'pdf' ? 'MinerU' : '—')}</p>
          <p className="break-all text-[13px] text-cream-muted">
            {url ? url.replace(/^https?:\/\//, '') : t('a.llm.noAddress')}{!s.url && s.defaultUrl ? ` (${t('a.llm.deployment')})` : ''}{s.ownKey ? ` · ${t('a.llm.ownKeyShort')}` : ''}
          </p>
          <p className="mt-0.5 text-[13px] text-cream-muted">
            {used.length ? t('a.llm.usedFor', { list: used.map((u) => t(USED_KEY[u] || u)).join(', ') }) : <span className="text-gold">{t('a.llm.notUsed')}</span>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 md:justify-end">
          <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[12.5px] ${TONE_CHIP[state.tone]}`}>
            {result?.status === 'testing' && <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />}
            {stateText(t, result)}
          </span>
          <button type="button" className={button} disabled={result?.status === 'testing' || !url} onClick={onTest}>{t('a.llm.test')}</button>
          <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}
            className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-[13px] text-cream-secondary hover:bg-ink-hover hover:text-cream cursor-pointer">
            {open ? t('close') : t('edit')}
            <Icon name="chevronRight" className={`h-3.5 w-3.5 transition-transform ${open ? '-rotate-90' : 'rotate-90'}`} />
          </button>
        </div>
      </div>
      {open && (
        <div className="grid gap-3 border-t border-border-subtle bg-ink-base px-3.5 pb-3.5 pt-3 md:grid-cols-2">
          {extra && (
            <label className="flex flex-col gap-1 md:col-span-2"><span className="text-[13px] text-cream-muted">{t('a.llm.name')}</span>
              <input id={`llm-name-${s.id}`} className={input} value={s.name} onChange={edit('name')} /></label>
          )}
          {s.kind !== 'pdf' && (
            <label className="flex flex-col gap-1"><span className="text-[13px] text-cream-muted">{t('a.llm.modelName')}</span>
              <input id={`llm-model-${s.id}`} className={input} value={s.model} onChange={edit('model')} /></label>
          )}
          <label className={`flex flex-col gap-1 ${s.kind === 'pdf' ? 'md:col-span-2' : ''}`}>
            <span className="text-[13px] text-cream-muted">{t('a.llm.address')}</span>
            <input id={`llm-url-${s.id}`} className={input} value={s.url} onChange={edit('url')}
              placeholder={s.kind === 'rerank' ? 'http://reranker:8000/rerank' : s.defaultUrl || 'http://host:8000/v1'} />
            {s.kind === 'pdf' && <span className="text-[13px] text-cream-muted">{t('a.llm.emptyDeployment')}{s.defaultUrl ? ` (${s.defaultUrl})` : ''}.</span>}
            {s.kind === 'rerank' && <span className="text-[13px] text-cream-muted">{t('a.llm.rerankAddress')}</span>}
          </label>
          <div className="flex flex-col gap-2 md:col-span-2">
            <div className="flex items-center gap-2.5 text-[14px] text-cream-secondary">
              <Switch on={s.ownKey} label={t('a.llm.ownKey')} onChange={setOwn} />{t('a.llm.ownKeyLong')}
            </div>
            {s.ownKey && (
              <input id={`llm-key-${s.id}`} className={input} value={s.key} onChange={edit('key')} autoComplete="off"
                placeholder={t('a.llm.ownKeyPlaceholder')} />
            )}
          </div>
          {extra && (
            <div className="flex flex-wrap items-center gap-2 md:col-span-2">
              <button type="button" className={`${button} !text-danger`} disabled={used.length > 0} onClick={remove}>{t('a.llm.remove')}</button>
              {used.length > 0 && <span className="text-[13px] text-cream-muted">{t('a.llm.stillUsed', { list: used.map((u) => t(USED_KEY[u] || u)).join(', ') })}</span>}
            </div>
          )}
          <p className="text-[13px] text-cream-muted md:col-span-2">{t(`a.kind.${s.kind}`)}. {t('a.llm.kindNote')}</p>
        </div>
      )}
    </div>
  );
}

export default function ServersSection({ st, set, results, test }) {
  const t = useT();
  const addServer = () => set((p) => ({ ...p, servers: [...p.servers,
    { id: `new-${Date.now()}`, kind: 'chat', name: t('a.llm.secondChat'), url: '', model: '', ownKey: false, key: '' }] }));
  const unused = unusedServers(st);
  return (
    <Card id="llm-servers" title={t('a.llm.servers')}
      text={t('a.llm.serversText')}
      action={<button type="button" className={button} onClick={() => st.servers.forEach((s) => test(s.id))}>
        <Icon name="refresh" className="h-3.5 w-3.5" />{t('a.llm.testAll')}</button>}>
      <KeyBox st={st} set={set} />
      <div className="overflow-hidden rounded-xl border border-border-subtle">
        {st.servers.map((s) => <ServerRow key={s.id} st={st} set={set} server={s} result={results[s.id]} onTest={() => test(s.id)} />)}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-[13px] text-cream-muted">
        <button type="button" onClick={addServer} disabled={unused.length > 0}
          className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-[13px] text-cream-secondary hover:bg-ink-hover hover:text-cream cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed">
          <Icon name="plus" className="h-3.5 w-3.5" />{t('a.llm.addServer')}</button>
        {t('a.llm.addServerNote')}
      </div>
    </Card>
  );
}
