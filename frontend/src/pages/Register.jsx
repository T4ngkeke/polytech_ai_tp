/**
 * Register.jsx — Student self-registration page for Edu-LLM.
 */

import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import ThemeToggle from '../components/ThemeToggle';
import PasswordInput from '../components/PasswordInput';
import { useT } from '../lib/i18n';

const FIELD = 'w-full rounded-xl border border-border-default bg-ink-raised px-3 py-2 text-[14px] text-cream placeholder:text-cream-muted focus:outline-none focus:border-cream-secondary';

export default function Register() {
  const t = useT();
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    if (password !== confirm) {
      setError(t('password.mismatch'));
      return;
    }
    if (password.length < 6) {
      setError(t('password.short'));
      return;
    }

    setIsLoading(true);
    try {
      const res = await fetch('/api/auth/signup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || t('p.register.failed'));
      }

      toast.success(t('p.register.done'));
      navigate('/login', { replace: true });
    } catch (err) {
      setError(err.message || t('p.register.failed'));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center bg-ink-deep px-4 py-8">
      <ThemeToggle className="absolute right-4 top-4" />
      <div className="w-full max-w-[380px]">
        <div className="mb-6 flex items-center justify-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-ink text-[15px] font-semibold text-on-ink">E</span>
          <span className="text-[18px] font-semibold text-cream">Edu-LLM</span>
        </div>
        <div className="rounded-2xl border border-border-subtle bg-ink-raised p-6">
          <h1 className="text-[15px] font-semibold text-cream">{t('p.register.title')}</h1>
          <p className="mt-1 text-[13px] text-cream-muted">{t('p.register.subtitle')}</p>

          <form onSubmit={handleSubmit} className="mt-5 space-y-4">
            {error && (
              <div role="alert" className="rounded-xl border border-danger/20 bg-danger-muted px-3 py-2 text-[13px] text-danger">
                {error}
              </div>
            )}

            <div className="space-y-1.5">
              <label htmlFor="reg-username" className="block text-[13px] text-cream-muted">{t('account.username')}</label>
              <input id="reg-username" type="text" required autoComplete="username"
                value={username} onChange={(e) => setUsername(e.target.value)}
                placeholder={t('p.register.usernameHint')} className={FIELD} />
            </div>

            <div className="space-y-1.5">
              <label htmlFor="reg-password" className="block text-[13px] text-cream-muted">{t('p.login.password')}</label>
              <PasswordInput id="reg-password" required autoComplete="new-password"
                value={password} onChange={(e) => setPassword(e.target.value)}
                placeholder={t('password.short')} className={FIELD} />
            </div>

            <div className="space-y-1.5">
              <label htmlFor="reg-confirm" className="block text-[13px] text-cream-muted">{t('p.register.confirm')}</label>
              <PasswordInput id="reg-confirm" required autoComplete="new-password"
                value={confirm} onChange={(e) => setConfirm(e.target.value)} className={FIELD} />
            </div>

            <button id="register-submit" type="submit" disabled={isLoading}
              className="w-full cursor-pointer rounded-full bg-ink px-4 py-2 text-[14px] text-on-ink transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60">
              {isLoading ? t('p.register.busy') : t('p.register.submit')}
            </button>
          </form>

          <p className="mt-5 text-center text-[13px] text-cream-muted">
            {t('p.register.haveAccount')}{' '}
            <Link to="/login" className="text-cream-secondary underline">{t('p.login.submit')}</Link>
          </p>
        </div>
      </div>
    </div>
  );
}
