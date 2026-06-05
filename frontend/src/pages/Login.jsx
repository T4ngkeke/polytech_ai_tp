/**
 * Login.jsx — Full-screen login page for Edu-LLM v6.
 * "Dossier Noir" aesthetic: dark editorial with cyan CTA.
 */

import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import useAuthStore, { DEFAULT_ROUTES } from '../store/authStore';

export default function Login() {
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
        throw new Error(body.detail || 'Login failed');
      }

      const { access_token } = await res.json();
      const role = await login(access_token);
      const target = DEFAULT_ROUTES[role] || '/chat';
      toast.success('Welcome back.');
      navigate(target, { replace: true });
    } catch (err) {
      setError(err.message || 'Login failed. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center relative overflow-hidden bg-ink-deep">
      {/* ── Background decoration ── */}
      <div className="absolute inset-0 pointer-events-none select-none">
        {/* Warm cyan glow */}
        <div className="absolute top-[-25%] left-[-15%] w-[700px] h-[700px] rounded-full bg-cyan/[0.04] blur-[140px]" />
        <div className="absolute bottom-[-20%] right-[-10%] w-[500px] h-[500px] rounded-full bg-gold/[0.03] blur-[120px]" />
        {/* Subtle grid */}
        <div
          className="absolute inset-0 opacity-[0.03]"
          style={{
            backgroundImage:
              'linear-gradient(var(--color-cream-muted) 1px, transparent 1px), linear-gradient(90deg, var(--color-cream-muted) 1px, transparent 1px)',
            backgroundSize: '80px 80px',
          }}
        />
      </div>

      {/* ── Login Card ── */}
      <div className="relative z-10 w-full max-w-md px-4 animate-fade-in">
        <div className="bg-ink-base rounded-2xl border border-border-subtle shadow-elevated overflow-hidden noise">
          {/* Card Header */}
          <div className="relative z-10 px-8 pt-10 pb-6 text-center">
            {/* Logo */}
            <div className="mx-auto w-14 h-14 rounded-xl gradient-cyan flex items-center justify-center shadow-glow mb-5">
              <svg className="w-7 h-7 text-cream" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 2L14.09 8.26L20 9.27L15.55 13.97L16.91 20L12 16.9L7.09 20L8.45 13.97L4 9.27L9.91 8.26L12 2Z" />
              </svg>
            </div>
            <h1 className="text-3xl font-display text-cream mb-1.5 tracking-tight">
              Edu-LLM
            </h1>
            <p className="text-sm text-cream-secondary">
              Sign in to access your learning environment
            </p>
          </div>

          {/* Form */}
          <form onSubmit={handleSubmit} className="relative z-10 px-8 pb-10 space-y-5">
            {/* Error banner */}
            {error && (
              <div className="flex items-center gap-2.5 px-4 py-3 rounded-lg bg-danger-muted border border-danger/20 text-danger text-sm animate-fade-in">
                <svg className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="10" /><path d="m15 9-6 6M9 9l6 6" />
                </svg>
                {error}
              </div>
            )}

            {/* Username */}
            <div className="space-y-1.5">
              <label htmlFor="login-username" className="mono-label">
                Username
              </label>
              <input
                id="login-username"
                type="text"
                required
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Enter your username"
                className="w-full px-4 py-3 rounded-lg bg-ink-deep border border-border-default text-cream text-sm placeholder:text-cream-muted focus:outline-none focus:border-cyan focus:ring-1 focus:ring-cyan/30 transition-colors"
              />
            </div>

            {/* Password */}
            <div className="space-y-1.5">
              <label htmlFor="login-password" className="mono-label">
                Password
              </label>
              <input
                id="login-password"
                type="password"
                required
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter your password"
                className="w-full px-4 py-3 rounded-lg bg-ink-deep border border-border-default text-cream text-sm placeholder:text-cream-muted focus:outline-none focus:border-cyan focus:ring-1 focus:ring-cyan/30 transition-colors"
              />
            </div>

            {/* Submit */}
            <button
              id="login-submit"
              type="submit"
              disabled={isLoading}
              className="w-full py-3 rounded-lg gradient-cyan text-cream text-sm font-semibold tracking-wide transition-all duration-200 hover:brightness-110 active:scale-[0.98] disabled:opacity-60 disabled:cursor-not-allowed cursor-pointer shadow-glow"
            >
              {isLoading ? (
                <span className="flex items-center justify-center gap-2">
                  <div className="w-4 h-4 rounded-full border-2 border-cream/30 border-t-cream animate-[spin_0.8s_linear_infinite]" />
                  Signing in…
                </span>
              ) : (
                'Sign In'
              )}
            </button>
          </form>

          {/* Register link */}
          <div className="px-8 pb-8 text-center">
            <p className="text-xs text-cream-muted">
              New student?{' '}
              <Link to="/register" className="text-cyan hover:underline">Create an account</Link>
            </p>
          </div>
        </div>

        {/* Footer */}
        <p className="text-center text-[11px] text-cream-muted mt-6 font-body tracking-wide">
          Edu-LLM v6 Class-Lab Architecture — Polytech AI
        </p>
      </div>
    </div>
  );
}
