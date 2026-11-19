/**
 * admin/llm/ServersSection.jsx — "Model servers" of the Model settings page:
 * the shared key, one row per server with its test result, and "Add a server"
 * for a second chat model. State lives in LlmConfigTab.
 */
import { useState } from 'react';
import Icon from '../../../components/Icon';
import { SERVER_KINDS, serverUrl, testState, unusedServers, usedFor } from '../../../lib/llmSettings';
import { Card, Switch } from './parts';

const TONE_CHIP = {
  ok: 'bg-success-muted text-success', bad: 'bg-danger-muted text-danger', warn: 'bg-gold-muted text-gold',
  muted: 'bg-ink-surface text-cream-muted', accent: 'bg-cyan-muted text-cyan',
};

const input = 'w-full min-w-0 px-3 py-2 rounded-lg bg-ink-deep border border-border-default text-cream text-sm font-mono placeholder:text-cream-muted focus:outline-none focus:border-cyan/50';
const button = 'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border-default bg-ink-raised text-sm font-medium text-cream-secondary hover:bg-ink-hover hover:text-cream cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed';

function KeyBox({ st, set }) {
  const [show, setShow] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const use = () => { set((p) => ({ ...p, key: draft.trim() })); setEditing(false); setDraft(''); setShow(false); };
  return (
    <div className="flex flex-col gap-3 rounded-xl bg-ink-surface p-3.5 sm:flex-row sm:items-center">
      <Icon name="key" className="hidden h-4 w-4 shrink-0 text-cyan sm:block" />
      <div className="min-w-0 flex-1">
        <p className="mono-label">Shared key of the model servers</p>
        {editing ? (
          <div className="mt-1.5 flex flex-wrap gap-2">
            <input id="llm-key-new" className={`${input} flex-1`} value={draft} autoComplete="off"
              onChange={(e) => setDraft(e.target.value)} placeholder="Paste the new key" />
            <button type="button" className={`${button} !bg-cyan !text-on-accent !border-transparent`} disabled={!draft.trim()} onClick={use}>Use this key</button>
            <button type="button" className={button} onClick={() => { setEditing(false); setDraft(''); }}>Cancel</button>
          </div>
        ) : (
          <p className="mt-1 break-all font-mono text-[13px] text-cream">
            {st.key ? (show ? st.key : `${'•'.repeat(16)} …${st.key.slice(-4)}`) : <span className="text-danger">No key set</span>}
          </p>
        )}
        <p className="mt-1 text-xs leading-relaxed text-cream-muted">
          Every server below sends this key, unless it has its own. The servers read theirs from
          <span className="font-mono"> key.env</span> on the host (documentation/MODEL_SERVERS.md): change both together.
        </p>
      </div>
      {!editing && (
        <div className="flex gap-2">
          <button type="button" className={button} onClick={() => setShow(!show)}>
            <Icon name={show ? 'eyeOff' : 'eye'} className="h-3.5 w-3.5" />{show ? 'Hide' : 'Show'}
          </button>
          <button type="button" className={button} onClick={() => setEditing(true)}>Change</button>
        </div>
      )}
    </div>
  );
}

function ServerRow({ st, set, server: s, result, onTest }) {
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
          <Icon name="server" className="h-4 w-4 shrink-0 text-cyan" />{s.name || 'New server'}
        </div>
        <div className="min-w-0">
          <p className="break-all font-mono text-[12.5px] text-cream">{s.model || (s.kind === 'pdf' ? 'MinerU' : '—')}</p>
          <p className="break-all font-mono text-xs text-cream-muted">
            {url ? url.replace(/^https?:\/\//, '') : 'no address'}{!s.url && s.defaultUrl ? ' (deployment setting)' : ''}{s.ownKey ? ' · own key' : ''}
          </p>
          <p className="mt-0.5 text-xs text-cream-muted">
            {used.length ? `Used for: ${used.join(', ')}` : <span className="text-gold">Not used by any feature: not kept when you save.</span>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 md:justify-end">
          <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${TONE_CHIP[state.tone]}`}>
            {result?.status === 'testing' && <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />}
            {state.text}
          </span>
          <button type="button" className={button} disabled={result?.status === 'testing' || !url} onClick={onTest}>Test</button>
          <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}
            className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm text-cyan hover:bg-cyan-muted cursor-pointer">
            {open ? 'Close' : 'Edit'}
            <Icon name="chevronRight" className={`h-3.5 w-3.5 transition-transform ${open ? '-rotate-90' : 'rotate-90'}`} />
          </button>
        </div>
      </div>
      {open && (
        <div className="grid gap-3 border-t border-dashed border-border-subtle bg-ink-base px-3.5 pb-3.5 pt-3 md:grid-cols-2">
          {extra && (
            <label className="flex flex-col gap-1 md:col-span-2"><span className="mono-label">Name</span>
              <input id={`llm-name-${s.id}`} className={input} value={s.name} onChange={edit('name')} /></label>
          )}
          {s.kind !== 'pdf' && (
            <label className="flex flex-col gap-1"><span className="mono-label">Model name</span>
              <input id={`llm-model-${s.id}`} className={input} value={s.model} onChange={edit('model')} /></label>
          )}
          <label className={`flex flex-col gap-1 ${s.kind === 'pdf' ? 'md:col-span-2' : ''}`}>
            <span className="mono-label">Address</span>
            <input id={`llm-url-${s.id}`} className={input} value={s.url} onChange={edit('url')}
              placeholder={s.kind === 'rerank' ? 'http://reranker:8000/rerank' : s.defaultUrl || 'http://host:8000/v1'} />
            {s.kind === 'pdf' && <span className="text-xs text-cream-muted">Empty: the deployment setting{s.defaultUrl ? ` (${s.defaultUrl})` : ''}.</span>}
            {s.kind === 'rerank' && <span className="text-xs text-cream-muted">The reranker’s /rerank address, not a chat address.</span>}
          </label>
          <div className="flex flex-col gap-2 md:col-span-2">
            <div className="flex items-center gap-2.5 text-sm text-cream-secondary">
              <Switch on={s.ownKey} label="Use its own key" onChange={setOwn} />Use its own key instead of the shared one
            </div>
            {s.ownKey && (
              <input id={`llm-key-${s.id}`} className={input} value={s.key} onChange={edit('key')} autoComplete="off"
                placeholder="Key of this server only" />
            )}
          </div>
          {extra && (
            <div className="flex flex-wrap items-center gap-2 md:col-span-2">
              <button type="button" className={`${button} !text-danger`} disabled={used.length > 0} onClick={remove}>Remove this server</button>
              {used.length > 0 && <span className="text-xs text-cream-muted">Still used for {used.join(', ')}.</span>}
            </div>
          )}
          <p className="text-xs text-cream-muted md:col-span-2">{SERVER_KINDS[s.kind]}. The context length and memory of a server are set where it starts, not here.</p>
        </div>
      )}
    </div>
  );
}

export default function ServersSection({ st, set, results, test }) {
  const addServer = () => set((p) => ({ ...p, servers: [...p.servers,
    { id: `new-${Date.now()}`, kind: 'chat', name: 'Second chat model', url: '', model: '', ownKey: false, key: '' }] }));
  const unused = unusedServers(st);
  return (
    <Card id="llm-servers" title="Model servers"
      text="The servers the app calls, and whether they answer. Test sends the values on this page, saved or not."
      action={<button type="button" className={button} onClick={() => st.servers.forEach((s) => test(s.id))}>
        <Icon name="refresh" className="h-3.5 w-3.5" />Test all</button>}>
      <KeyBox st={st} set={set} />
      <div className="overflow-hidden rounded-xl border border-border-subtle">
        {st.servers.map((s) => <ServerRow key={s.id} st={st} set={set} server={s} result={results[s.id]} onTest={() => test(s.id)} />)}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs text-cream-muted">
        <button type="button" onClick={addServer} disabled={unused.length > 0}
          className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm text-cyan hover:bg-cyan-muted cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed">
          <Icon name="plus" className="h-3.5 w-3.5" />Add a server</button>
        a second chat model, to use for student answers or image views (below).
      </div>
    </Card>
  );
}
