/**
 * SettingsModal.jsx — the student's settings (2026-11 redesign): General
 * (language, theme), Personal ("about me", memory switch and the notes kept),
 * Data (export all conversations), Usage (day and week meters, reset times,
 * ask for a reset), Account (password). PUT /api/auth/settings saves the
 * language, "about me" and the memory switch.
 */
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import useAuthStore from '../../store/authStore';
import { useT } from '../../lib/i18n';
import { setTheme, themeChoice, useTheme } from '../../lib/theme';
import { percent } from '../../lib/quota';
import { downloadText } from '../../lib/exportFile';
import Icon from '../../components/Icon';
import LanguageSelect from '../../components/LanguageSelect';
import { Meter, Modal, Switch } from './ui';

const TABS = [
  { key: 'general', icon: 'settings' },
  { key: 'personal', icon: 'sparkles' },
  { key: 'data', icon: 'database' },
  { key: 'usage', icon: 'pulse' },
  { key: 'account', icon: 'key' },
];
const ABOUT_MAX = 500;

function Row({ label, children, hint }) {
  return (
    <div className="border-b border-border-subtle py-3.5 last:border-0">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-[14px] text-cream">{label}</span>
        {children}
      </div>
      {hint && <p className="mt-1 text-[12.5px] text-cream-muted">{hint}</p>}
    </div>
  );
}

function Segmented({ value, options, onChange, label }) {
  return (
    <div className="flex rounded-lg bg-ink-surface p-0.5" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={value === o.value} onClick={() => onChange(o.value)}
          className={`rounded-md px-3 py-1 text-[13px] ${value === o.value ? 'bg-ink-raised text-cream shadow-sm' : 'text-cream-secondary hover:text-cream'}`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

function General() {
  const t = useT();
  useTheme();
  const [theme, setChoice] = useState(themeChoice());
  return (
    <div>
      <Row label={t('settings.language')}>
        <LanguageSelect />
      </Row>
      <Row label={t('settings.theme')}>
        <Segmented value={theme} label={t('settings.theme')}
          options={[{ value: 'light', label: t('theme.light') }, { value: 'dark', label: t('theme.dark') }, { value: 'system', label: t('theme.system') }]}
          onChange={(v) => { setTheme(v); setChoice(v); }} />
      </Row>
    </div>
  );
}

function Personal() {
  const t = useT();
  const { aboutMe, memoryEnabled, saveSettings } = useAuthStore();
  const [text, setText] = useState(aboutMe);
  const [memories, setMemories] = useState(null);
  const load = () => api.get('/api/student/memories').then((d) => setMemories(d.items)).catch(() => setMemories([]));
  useEffect(() => { load(); }, []);
  const save = async () => {
    try { await saveSettings({ about_me: text.trim() || null }); toast.success(t('saved')); } catch { toast.error(t('error.save')); }
  };
  const remove = async (id) => {
    await api.delete(id ? `/api/student/memories/${id}` : '/api/student/memories').catch(() => {});
    load();
  };
  return (
    <div>
      <div className="py-3.5">
        <label htmlFor="about-me" className="text-[14px] text-cream">{t('about.title')}</label>
        <p className="mb-2 mt-0.5 text-[12.5px] text-cream-muted">{t('about.help')}</p>
        <textarea id="about-me" value={text} maxLength={ABOUT_MAX} rows={4} onChange={(e) => setText(e.target.value)}
          placeholder={t('about.placeholder')}
          className="w-full resize-y rounded-xl border border-border-default bg-ink-raised px-3 py-2 text-[14px] text-cream outline-none focus:border-cream-muted" />
        <div className="mt-2 flex items-center justify-between">
          <span className="text-[12px] tabular-nums text-cream-muted">{text.length} / {ABOUT_MAX}</span>
          <button type="button" onClick={save} disabled={text === aboutMe}
            className="rounded-full bg-ink px-3.5 py-1.5 text-[13px] text-on-ink disabled:opacity-40">{t('save')}</button>
        </div>
      </div>
      <Row label={t('memory.title')} hint={t('memory.help')}>
        <Switch id="memory-switch" checked={memoryEnabled} label={t('memory.title')}
          onChange={(v) => saveSettings({ memory_enabled: v }).catch(() => toast.error(t('error.save')))} />
      </Row>
      <div className="py-2">
        {memories === null && <p className="text-[13px] text-cream-muted">…</p>}
        {memories?.length === 0 && <p className="text-[13px] text-cream-muted">{t('memory.none')}</p>}
        {memories?.map((m) => (
          <div key={m.id} className="group flex items-start gap-2 border-b border-border-subtle py-2 last:border-0">
            <div className="min-w-0 flex-1">
              <p className="text-[13.5px] text-cream">{m.content}</p>
              <p className="text-[12px] text-cream-muted">{[m.class_name, m.category].filter(Boolean).join(' · ')}</p>
            </div>
            <button type="button" onClick={() => remove(m.id)} aria-label={t('delete')}
              className="rounded p-1 text-cream-muted hover:text-danger"><Icon name="trash" className="h-4 w-4" /></button>
          </div>
        ))}
        {memories?.length > 1 && (
          <button type="button" onClick={() => remove(null)} className="mt-2 text-[13px] text-danger hover:underline">{t('memory.deleteAll')}</button>
        )}
      </div>
    </div>
  );
}

function Data() {
  const t = useT();
  const [format, setFormat] = useState('md');
  const [busy, setBusy] = useState(false);
  const download = async () => {
    setBusy(true);
    try {
      const res = await api.raw(`/api/student/export?format=${format}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      downloadText(`edu-llm-${new Date().toISOString().slice(0, 10)}.${format}`, await res.text(),
        format === 'md' ? 'text/markdown;charset=utf-8' : 'application/json;charset=utf-8');
    } catch (err) { toast.error(err.message); } finally { setBusy(false); }
  };
  return (
    <div>
      <Row label={t('export.title')} hint={t('export.help')}>
        <div className="flex items-center gap-2">
          <Segmented value={format} label={t('export.title')} onChange={setFormat}
            options={[{ value: 'md', label: 'Markdown' }, { value: 'json', label: 'JSON' }]} />
          <button type="button" onClick={download} disabled={busy}
            className="rounded-full bg-ink px-3.5 py-1.5 text-[13px] text-on-ink disabled:opacity-40">{t('export.button')}</button>
        </div>
      </Row>
      <p className="py-3 text-[12.5px] text-cream-muted">{t('data.teacherSees')}</p>
    </div>
  );
}

function Usage({ usage, onAskReset, resetAsked }) {
  const t = useT();
  const until = (iso) => {
    const ms = new Date(iso).getTime() - Date.now();
    if (!(ms > 0)) return t('time.soon');
    const hours = Math.round(ms / 3_600_000);
    return hours < 48 ? t('time.hours', { n: Math.max(1, hours) }) : t('time.days', { n: Math.round(hours / 24) });
  };
  return (
    <div className="space-y-5 py-3">
      <div>
        <Meter label={t('quota.today')} value={percent(usage.used, usage.limit)} />
        {usage.resets_at && <p className="mt-1.5 text-[12.5px] text-cream-muted">{t('quota.dayResets', { when: until(usage.resets_at) })}</p>}
      </div>
      <div>
        <Meter label={t('quota.week')} value={percent(usage.week_used, usage.week_limit)} />
        {usage.week_resets_at && <p className="mt-1.5 text-[12.5px] text-cream-muted">{t('quota.weekResets', { when: until(usage.week_resets_at) })}</p>}
      </div>
      <p className="text-[13px] text-cream-secondary">{t('quota.sentence')}</p>
      {usage.blocked && usage.reset_state !== 'ignored' && (
        usage.reset_requested || resetAsked
          ? <p className="text-[13px] text-cream-secondary">{t('quota.requestSent')}</p>
          : <button type="button" onClick={onAskReset} className="rounded-full bg-ink px-3.5 py-1.5 text-[13px] text-on-ink">{t('quota.askReset')}</button>
      )}
    </div>
  );
}

function Account() {
  const t = useT();
  const username = useAuthStore((s) => s.username);
  const [old, setOld] = useState('');
  const [next, setNext] = useState('');
  const [again, setAgain] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setError('');
    if (next.length < 6) { setError(t('password.short')); return; }
    if (next !== again) { setError(t('password.mismatch')); return; }
    setBusy(true);
    try {
      await api.post('/api/auth/change-password', { old_password: old, new_password: next });
      toast.success(t('password.changed'));
      setOld(''); setNext(''); setAgain('');
    } catch (err) {
      setError(err.message === 'Old password is incorrect' ? t('password.wrongOld') : err.message);
    } finally { setBusy(false); }
  };
  const field = 'mt-1 w-full rounded-xl border border-border-default bg-ink-raised px-3 py-2 text-[14px] text-cream outline-none focus:border-cream-muted';
  return (
    <div>
      <Row label={t('account.username')}><span className="text-[14px] text-cream-secondary">{username}</span></Row>
      <form onSubmit={submit} className="max-w-sm space-y-3 py-3.5">
        <div className="text-[14px] font-medium text-cream">{t('password.change')}</div>
        <label className="block text-[13px] text-cream-secondary" htmlFor="pw-old">{t('password.old')}
          <input id="pw-old" type="password" autoComplete="current-password" value={old} onChange={(e) => setOld(e.target.value)} className={field} /></label>
        <label className="block text-[13px] text-cream-secondary" htmlFor="pw-new">{t('password.new')}
          <input id="pw-new" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} className={field} /></label>
        <label className="block text-[13px] text-cream-secondary" htmlFor="pw-again">{t('password.again')}
          <input id="pw-again" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} className={field} /></label>
        {error && <p role="alert" className="text-[13px] text-danger">{error}</p>}
        <button type="submit" disabled={busy || !old || !next} className="rounded-full bg-ink px-4 py-2 text-[13.5px] text-on-ink disabled:opacity-40">{t('password.save')}</button>
      </form>
    </div>
  );
}

export default function SettingsModal({ tab: firstTab = 'general', usage, onClose, onAskReset, resetAsked, tabs = TABS.map((x) => x.key) }) {
  const t = useT();
  const [tab, setTab] = useState(firstTab);
  const shown = TABS.filter((x) => tabs.includes(x.key));
  return (
    <Modal title={t('settings.title')} onClose={onClose}>
      <div className="flex min-h-0 flex-1 flex-col sm:flex-row">
        <nav className="flex shrink-0 gap-1 overflow-x-auto px-3 pb-2 sm:w-44 sm:flex-col sm:px-3" aria-label={t('settings.title')}>
          {shown.map((x) => (
            <button key={x.key} type="button" onClick={() => setTab(x.key)} aria-current={tab === x.key ? 'page' : undefined}
              className={`flex shrink-0 items-center gap-2 rounded-lg px-2.5 py-2 text-[14px] ${tab === x.key ? 'bg-ink-hover text-cream' : 'text-cream-secondary hover:bg-ink-hover hover:text-cream'}`}>
              <Icon name={x.icon} className="h-4 w-4" />{t(`settings.tab.${x.key}`)}
            </button>
          ))}
        </nav>
        <div className="min-h-[340px] flex-1 overflow-y-auto px-5 pb-5">
          {tab === 'general' && <General />}
          {tab === 'personal' && <Personal />}
          {tab === 'data' && <Data />}
          {tab === 'usage' && usage && <Usage usage={usage} onAskReset={onAskReset} resetAsked={resetAsked} />}
          {tab === 'account' && <Account />}
        </div>
      </div>
    </Modal>
  );
}
