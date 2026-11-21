/**
 * ChoosePassword.jsx — shown instead of every page after an admin reset the
 * user's password (to 123456, users.must_change_password). The user chooses
 * a new password or signs out; until then the server answers 403
 * password_change_required everywhere else. The old password is not asked:
 * it is the known reset one. Agreed on a prototype first (2026-11-17).
 */

import { useState } from 'react';
import toast from 'react-hot-toast';

import api from '../lib/api';
import useAuthStore from '../store/authStore';
import { useT } from '../lib/i18n';

const FIELD = 'w-full rounded-xl border border-border-default bg-ink-raised px-3 py-2 text-[14px] text-cream focus:outline-none focus:border-cream-secondary';

// Server codes → our keys.
const MESSAGES = {
  choose_another_password: 'p.choose.another',
};

export default function ChoosePassword() {
  const t = useT();
  const { username, logout } = useAuthStore();
  const [password, setPassword] = useState('');
  const [again, setAgain] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    const problem = password.length < 6 ? t('password.short')
      : password !== again ? t('password.mismatch') : '';
    setError(problem);
    if (problem) return;
    setSaving(true);
    try {
      await api.post('/api/auth/change-password', { new_password: password });
      useAuthStore.setState({ mustChangePassword: false });
      toast.success(t('password.changed'));
    } catch (err) {
      setError(MESSAGES[err.message] ? t(MESSAGES[err.message]) : err.message || t('p.choose.failed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex h-screen items-center justify-center overflow-y-auto bg-ink-deep px-4 py-8">
      <div className="w-full max-w-[380px]">
        <div className="mb-6 flex items-center justify-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-ink text-[15px] font-semibold text-on-ink">E</span>
          <span className="text-[18px] font-semibold text-cream">Edu-LLM</span>
        </div>
        <form onSubmit={submit} className="space-y-4 rounded-2xl border border-border-subtle bg-ink-raised p-6">
          <h1 className="text-[15px] font-semibold text-cream">{t('p.choose.title')}</h1>
          <p className="rounded-xl bg-warn-soft px-3 py-2.5 text-[13px] text-cream-secondary">
            {t('p.choose.notice', { name: username })}
          </p>
          <Field id="choose-password" label={t('p.choose.new')} value={password} onChange={setPassword} autoFocus />
          <Field id="choose-password-again" label={t('password.again')} value={again} onChange={setAgain} />
          {error && <p className="text-[13px] text-danger" role="alert">{error}</p>}
          <button type="submit" disabled={saving}
            className="w-full cursor-pointer rounded-full bg-ink px-4 py-2 text-[14px] text-on-ink transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60">
            {saving ? t('p.choose.saving') : t('p.choose.submit')}
          </button>
          <button type="button" onClick={() => { logout(); window.location.href = '/login'; }}
            className="w-full cursor-pointer rounded-full px-4 py-2 text-[14px] text-cream-secondary hover:bg-ink-hover">
            {t('logout')}
          </button>
        </form>
      </div>
    </div>
  );
}

function Field({ id, label, value, onChange, autoFocus = false }) {
  return (
    <div className="space-y-1.5">
      <label className="block text-[13px] text-cream-muted" htmlFor={id}>{label}</label>
      <input id={id} type="password" value={value} autoFocus={autoFocus} autoComplete="new-password"
        onChange={(e) => onChange(e.target.value)} className={FIELD} />
    </div>
  );
}
