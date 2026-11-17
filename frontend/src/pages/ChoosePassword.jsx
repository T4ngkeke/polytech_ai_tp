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

const MESSAGES = {
  choose_another_password: 'Choose a password other than the one the admin gave you.',
};

export default function ChoosePassword() {
  const { username, logout } = useAuthStore();
  const [password, setPassword] = useState('');
  const [again, setAgain] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    const problem = password.length < 6 ? 'At least 6 characters.'
      : password !== again ? 'The two passwords differ.' : '';
    setError(problem);
    if (problem) return;
    setSaving(true);
    try {
      await api.post('/api/auth/change-password', { new_password: password });
      useAuthStore.setState({ mustChangePassword: false });
      toast.success('Password changed');
    } catch (err) {
      setError(MESSAGES[err.message] || err.message || 'Could not change the password');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex h-screen items-center justify-center overflow-y-auto bg-ink-deep px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4">
        <h1 className="text-xl font-semibold text-cream">Choose a new password</h1>
        <p className="rounded-lg bg-gold-muted px-3 py-2.5 text-sm text-cream-secondary">
          An admin reset the password of <span className="font-medium text-cream">{username}</span>.
          Choose your own password to continue.
        </p>
        <Field id="choose-password" label="New password" value={password} onChange={setPassword} autoFocus />
        <Field id="choose-password-again" label="New password again" value={again} onChange={setAgain} />
        {error && <p className="text-xs text-danger" role="alert">{error}</p>}
        <button type="submit" disabled={saving}
          className="w-full py-3 rounded-lg gradient-cyan text-on-accent text-sm font-semibold hover:brightness-110 transition-all cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed">
          {saving ? 'Saving…' : 'Save and continue'}
        </button>
        <button type="button" onClick={() => { logout(); window.location.href = '/login'; }}
          className="w-full py-2 rounded-lg text-sm text-cream-secondary hover:bg-ink-hover cursor-pointer">
          Sign out
        </button>
      </form>
    </div>
  );
}

function Field({ id, label, value, onChange, autoFocus = false }) {
  return (
    <div className="space-y-1.5">
      <label className="mono-label" htmlFor={id}>{label}</label>
      <input id={id} type="password" value={value} autoFocus={autoFocus} autoComplete="new-password"
        onChange={(e) => onChange(e.target.value)}
        className="w-full px-4 py-3 rounded-lg bg-ink-deep border border-border-default text-cream text-sm focus:outline-none focus:border-cyan/40 font-mono transition-colors" />
    </div>
  );
}
