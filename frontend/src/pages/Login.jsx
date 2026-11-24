/**
 * Login.jsx — Full-screen login page for Edu-LLM.
 * A centered card on a plain page: the product mark, two fields, one black button.
 */

import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import useAuthStore, { DEFAULT_ROUTES } from '../store/authStore';
import ThemeToggle from '../components/ThemeToggle';
import PasswordInput from '../components/PasswordInput';
import { useT } from '../lib/i18n';

const FIELD = 'w-full rounded-xl border border-border-default bg-ink-raised px-3 py-2 text-[14px] text-cream placeholder:text-cream-muted focus:outline-none focus:border-cream-secondary';

export default function Login() {
  const t = useT();
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setIsLoading(true);

    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || t('p.login.failed'));
      }

      const { access_token } = await res.json();
      const role = await login(access_token);
      const target = DEFAULT_ROUTES[role] || '/chat';
      toast.success(t('p.login.welcome'));
      navigate(target, { replace: true });
    } catch (err) {
      setError(err.message || t('p.login.failedRetry'));
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
          <h1 className="text-[15px] font-semibold text-cream">{t('p.login.title')}</h1>
          <p className="mt-1 text-[13px] text-cream-muted">{t('p.login.subtitle')}</p>

          <form onSubmit={handleSubmit} className="mt-5 space-y-4">
            {error && (
              <div role="alert" className="rounded-xl border border-danger/20 bg-danger-muted px-3 py-2 text-[13px] text-danger">
                {error}
              </div>
            )}

            <div className="space-y-1.5">
              <label htmlFor="login-username" className="block text-[13px] text-cream-muted">
                {t('account.username')}
              </label>
              <input
                id="login-username"
                type="text"
                required
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className={FIELD}
              />
            </div>

            <div className="space-y-1.5">
              <label htmlFor="login-password" className="block text-[13px] text-cream-muted">
                {t('p.login.password')}
              </label>
              <PasswordInput
                id="login-password"
                required
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={FIELD}
              />
            </div>

            <button
              id="login-submit"
              type="submit"
              disabled={isLoading}
              className="w-full cursor-pointer rounded-full bg-ink px-4 py-2 text-[14px] text-on-ink transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isLoading ? t('p.login.busy') : t('p.login.submit')}
            </button>
          </form>

          <p className="mt-5 text-center text-[13px] text-cream-muted">
            {t('p.login.newStudent')}{' '}
            <Link to="/register" className="text-cream-secondary underline">{t('p.login.create')}</Link>
          </p>
        </div>
        <p className="mt-5 text-center text-[12px] text-cream-muted">Edu-LLM · Polytech</p>
      </div>
    </div>
  );
}
