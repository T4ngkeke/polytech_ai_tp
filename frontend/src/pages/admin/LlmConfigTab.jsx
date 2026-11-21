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
import { useT } from '../../lib/i18n';
import { PageHeader, Spinner, primaryButton, outlineButton } from './shared';
import ServersSection from './llm/ServersSection';
import UsesSection from './llm/UsesSection';
import { AnswersSection, BillingSection, PdfSection } from './llm/SettingsSections';

const FALLBACK_CONTEXT = 65536;  // as the chat when the server does not say
const signature = (st, s) => `${serverUrl(s)}|${s.kind === 'pdf' ? '' : s.model}|${serverKey(st, s)}`;

export default function LLMConfigTab({ onDirtyChange }) {
  const t = useT();
  const [config, setConfig] = useState(null);
  const [st, setSt] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [saving, setSaving] = useState(false);
  const [showList, setShowList] = useState(false);
  const [results, setResults] = useState({});  // id -> { ...check result, sig } or { status: 'testing', sig }
  const stRef = useRef(null);
  const rootRef = useRef(null);
  const tRef = useRef(t);  // the language changing must not reload the page
  tRef.current = t;
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
      setLoadError(err.message || tRef.current('a.llm.loadFail'));
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
      <button type="button" onClick={load} className={`mt-3 cursor-pointer ${outlineButton}`}>{t('a.llm.tryAgain')}</button>
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
    if (invalid) { invalid.focus(); toast.error(t('a.llm.invalid')); return; }
    const failing = st.servers.filter((s) => ['refused', 'model_missing', 'unreachable', 'invalid_url'].includes(shown[s.id]?.status));
    setSaving(true);
    try {
      const updated = await api.put('/api/admin/llm/config', payload(config, st));
      const next = fromConfig(updated);
      setConfig(updated); setSt(next); stRef.current = next; setShowList(false); setResults({});
      next.servers.forEach((s) => test(s.id));
      toast.success(failing.length
        ? t('a.llm.savedFailing', { names: failing.map((s) => s.name).join(', ') })
        : t('a.llm.saved'));
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto" ref={rootRef}>
      <div className="mx-auto flex max-w-4xl flex-col gap-5 p-4 md:p-8">
        <PageHeader title={t('a.llm.title')} subtitle={t('a.llm.subtitle')} />
        <ServersSection st={st} set={setSt} results={shown} test={test} />
        <UsesSection st={st} set={setSt} />
        <AnswersSection st={st} set={setSt} config={config} contextTokens={contextTokens} />
        <PdfSection st={st} set={setSt} config={config} />
        <BillingSection st={st} set={setSt} />
      </div>
      {dirty && (
        <div className="sticky bottom-0 z-10 flex justify-center px-4 pb-3 pt-2 md:px-8">
          <div role="region" aria-label={t('a.llm.unsaved')}
            className="flex w-full max-w-4xl flex-wrap items-center gap-2 rounded-2xl border border-border-default bg-ink-raised px-4 py-2.5 shadow-elevated">
            <span className="flex-1 text-[14px] text-cream">
              {t(list.length === 1 ? 'a.llm.unsavedOne' : 'a.llm.unsavedMany', { n: list.length })}
              <button type="button" onClick={() => setShowList(!showList)} className="ml-2 text-[13px] text-cream-secondary underline hover:text-cream cursor-pointer">
                {showList ? t('a.hide') : t('a.show')}</button>
            </span>
            <button type="button" disabled={saving} onClick={() => { setSt(saved); setShowList(false); }}
              className={`cursor-pointer ${outlineButton}`}>
              {t('a.llm.discard')}</button>
            <button type="button" disabled={saving} onClick={save}
              className={`cursor-pointer ${primaryButton}`}>
              {saving ? t('a.saving') : t('save')}</button>
            {showList && (
              <ul className="w-full list-disc pl-5 pt-1 text-[13px] text-cream-secondary">
                {list.map((c) => <li key={c}>{c}</li>)}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
