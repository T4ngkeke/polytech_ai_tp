/**
 * admin/LlmConfigTab.jsx — Admin "LLM Config" tab, the Model settings page:
 * model servers with one shared key, what each feature uses, answer limits,
 * PDF upload and billing, with one save bar for the whole page.
 *
 * The page state and its mapping to the stored settings are in
 * lib/llmSettings.js. A test sends the values on the page, saved or not
 * (POST /api/admin/llm/servers/check). Saving sends only the fields that
 * differ from what is stored (PUT /api/admin/llm/config).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import { changes, fromConfig, payload, serverKey, serverUrl } from '../../lib/llmSettings';
import { PageHeader, Spinner } from './shared';
import ServersSection from './llm/ServersSection';
import UsesSection from './llm/UsesSection';
import { AnswersSection, BillingSection, PdfSection } from './llm/SettingsSections';

const FALLBACK_CONTEXT = 65536;  // as the chat when the server does not say
const signature = (st, s) => `${serverUrl(s)}|${s.kind === 'pdf' ? '' : s.model}|${serverKey(st, s)}`;

export default function LLMConfigTab({ onDirtyChange }) {
  const [config, setConfig] = useState(null);
  const [st, setSt] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [saving, setSaving] = useState(false);
  const [showList, setShowList] = useState(false);
  const [results, setResults] = useState({});  // id -> { ...check result, sig } or { status: 'testing', sig }
  const stRef = useRef(null);
  const rootRef = useRef(null);
  stRef.current = st;

  const test = useCallback(async (id) => {
    const cur = stRef.current;
    const s = cur?.servers.find((x) => x.id === id);
    if (!s || !serverUrl(s)) return;
    const sig = signature(cur, s);
    setResults((r) => ({ ...r, [id]: { status: 'testing', sig } }));
    try {
      const res = await api.post('/api/admin/llm/servers/check',
        { url: serverUrl(s), model: s.kind === 'pdf' ? '' : s.model, api_key: serverKey(cur, s) });
      setResults((r) => ({ ...r, [id]: { ...res, sig } }));
    } catch (err) {
      setResults((r) => ({ ...r, [id]: { status: 'unreachable', sig } }));
      toast.error(err.message);
    }
  }, []);

  const load = useCallback(async () => {
    setLoadError('');
    try {
      const c = await api.get('/api/admin/llm/config');
      const next = fromConfig(c);
      setConfig(c); setSt(next); stRef.current = next; setResults({});
      next.servers.forEach((s) => test(s.id));
    } catch (err) {
      setLoadError(err.message || 'Could not load the model settings.');
    }
  }, [test]);
  useEffect(() => { load(); }, [load]);

  const saved = useMemo(() => (config ? fromConfig(config) : null), [config]);
  const list = useMemo(() => (saved && st ? changes(saved, st) : []), [saved, st]);
  const dirty = list.length > 0;
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);
  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (e) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  if (loadError) return (
    <div className="p-4 md:p-8" role="alert">
      <p className="text-danger">{loadError}</p>
      <button type="button" onClick={load} className="mt-3 text-cyan hover:underline cursor-pointer">Try again</button>
    </div>
  );
  if (!st) return <Spinner />;

  // A result counts only for the values it was made with; after a change the row says so.
  const shown = Object.fromEntries(st.servers.map((s) => {
    const r = results[s.id];
    if (!r) return [s.id, undefined];
    return [s.id, r.sig === signature(st, s) ? r : { status: 'changed' }];
  }));
  const answerServer = results[st.use.answers];
  const contextTokens = answerServer?.max_model_len || FALLBACK_CONTEXT;

  const save = async () => {
    const invalid = rootRef.current?.querySelector('[aria-invalid="true"]');
    if (invalid) { invalid.focus(); toast.error('A field in red is not valid. Fix it first.'); return; }
    const failing = st.servers.filter((s) => ['refused', 'model_missing', 'unreachable', 'invalid_url'].includes(shown[s.id]?.status));
    setSaving(true);
    try {
      const updated = await api.put('/api/admin/llm/config', payload(config, st));
      const next = fromConfig(updated);
      setConfig(updated); setSt(next); stRef.current = next; setShowList(false); setResults({});
      next.servers.forEach((s) => test(s.id));
      toast.success(failing.length
        ? `Saved. ${failing.map((s) => s.name).join(', ')} failed its last test.`
        : 'Saved. The changes apply to the next question.');
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto" ref={rootRef}>
      <div className="mx-auto flex max-w-4xl flex-col gap-5 p-4 md:p-8">
        <PageHeader title="Model settings" subtitle="Saved changes apply to the next question. Nothing restarts." />
        <ServersSection st={st} set={setSt} results={shown} test={test} />
        <UsesSection st={st} set={setSt} />
        <AnswersSection st={st} set={setSt} config={config} contextTokens={contextTokens} />
        <PdfSection st={st} set={setSt} config={config} />
        <BillingSection st={st} set={setSt} />
      </div>
      {dirty && (
        <div className="sticky bottom-0 z-10 flex justify-center px-4 pb-3 pt-2 md:px-8">
          <div role="region" aria-label="Unsaved changes"
            className="flex w-full max-w-4xl flex-wrap items-center gap-2 rounded-2xl border border-cyan/40 bg-ink-raised px-4 py-2.5 shadow-elevated">
            <span className="flex-1 text-sm text-cream">
              <b>{list.length}</b> unsaved change{list.length > 1 ? 's' : ''}
              <button type="button" onClick={() => setShowList(!showList)} className="ml-2 text-xs text-cyan hover:underline cursor-pointer">
                {showList ? 'Hide' : 'Show'}</button>
            </span>
            <button type="button" disabled={saving} onClick={() => { setSt(saved); setShowList(false); }}
              className="rounded-lg border border-border-default px-3 py-1.5 text-sm font-medium text-cream-secondary hover:bg-ink-hover cursor-pointer disabled:opacity-40">
              Discard</button>
            <button type="button" disabled={saving} onClick={save}
              className="rounded-lg gradient-cyan px-4 py-1.5 text-sm font-semibold text-on-accent hover:brightness-110 cursor-pointer disabled:opacity-40">
              {saving ? 'Saving…' : 'Save'}</button>
            {showList && (
              <ul className="w-full list-disc pl-5 pt-1 text-xs text-cream-secondary">
                {list.map((c) => <li key={c}>{c}</li>)}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
