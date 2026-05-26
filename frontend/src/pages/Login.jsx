/**
 * Login.jsx — Full-screen login page for Edu-LLM v3.
 *
 * Submits POST /api/auth/login, stores the JWT, fetches user profile
 * via the auth store, and redirects to the role-appropriate default page.
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
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
      // Step 1: Authenticate
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

      // Step 2: Store token and fetch user profile
      const role = await login(access_token);

      // Step 3: Redirect to role-based default route
      const target = DEFAULT_ROUTES[role] || '/chat';
      toast.success(`Welcome back!`);
      navigate(target, { replace: true });
    } catch (err) {
      setError(err.message || 'Login failed. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center relative overflow-hidden bg-bg-primary">
      {/* ── Background decoration ── */}
      <div className="absolute inset-0 pointer-events-none select-none">
        {/* Radial gradient blobs */}
        <div className="absolute top-[-20%] left-[-10%] w-[600px] h-[600px] rounded-full bg-accent/5 blur-[120px]" />
        <div className="absolute bottom-[-15%] right-[-5%] w-[500px] h-[500px] rounded-full bg-accent-warm/5 blur-[100px]" />
        {/* Grid pattern */}
        <div
          className="absolute inset-0 opacity-[0.03]"
          style={{
            backgroundImage:
              'linear-gradient(var(--color-text-muted) 1px, transparent 1px), linear-gradient(90deg, var(--color-text-muted) 1px, transparent 1px)',
            backgroundSize: '60px 60px',
          }}
        />
      </div>

      {/* ── Login Card ── */}
      <div className="relative z-10 w-full max-w-md px-4 animate-fade-in">
        <div className="bg-bg-surface rounded-2xl border border-border-subtle shadow-[var(--shadow-elevated)] overflow-hidden">
          {/* Card Header */}
          <div className="px-8 pt-10 pb-6 text-center">
            {/* Logo */}
            <div className="mx-auto w-14 h-14 rounded-2xl gradient-accent flex items-center justify-center shadow-[var(--shadow-glow)] mb-5">
              <svg className="w-7 h-7 text-white" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 2L14.09 8.26L20 9.27L15.55 13.97L16.91 20L12 16.9L7.09 20L8.45 13.97L4 9.27L9.91 8.26L12 2Z" />
              </svg>
            </div>
            <h1 className="text-2xl font-bold font-[var(--font-display)] text-text-primary mb-1">
              Welcome to Edu-LLM
            </h1>
            <p className="text-sm text-text-secondary">
              Sign in to access your AI learning environment
            </p>
          </div>

          {/* Form */}
          <form onSubmit={handleSubmit} className="px-8 pb-10 space-y-5">
            {/* Error banner */}
            {error && (
              <div className="flex items-center gap-2 px-4 py-3 rounded-lg bg-danger-muted border border-danger/20 text-danger text-sm animate-fade-in">
                <svg className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="10" /><path d="m15 9-6 6M9 9l6 6" />
                </svg>
                {error}
              </div>
            )}

            {/* Username */}
            <div className="space-y-1.5">
              <label htmlFor="login-username" className="block text-xs font-semibold uppercase tracking-wider text-text-muted">
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
                className="w-full px-4 py-3 rounded-lg bg-bg-primary border border-border-default text-text-primary text-sm placeholder:text-text-muted focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/30 transition-colors"
              />
            </div>

            {/* Password */}
            <div className="space-y-1.5">
              <label htmlFor="login-password" className="block text-xs font-semibold uppercase tracking-wider text-text-muted">
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
                className="w-full px-4 py-3 rounded-lg bg-bg-primary border border-border-default text-text-primary text-sm placeholder:text-text-muted focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/30 transition-colors"
              />
            </div>

            {/* Submit */}
            <button
              id="login-submit"
              type="submit"
              disabled={isLoading}
              className="w-full py-3 rounded-lg gradient-accent text-white text-sm font-semibold tracking-wide transition-all duration-200 hover:brightness-110 active:scale-[0.98] disabled:opacity-60 disabled:cursor-not-allowed cursor-pointer shadow-[var(--shadow-glow)]"
            >
              {isLoading ? (
                <span className="flex items-center justify-center gap-2">
                  <div className="w-4 h-4 rounded-full border-2 border-white/30 border-t-white animate-[spin_0.8s_linear_infinite]" />
                  Signing in…
                </span>
              ) : (
                'Sign In'
              )}
            </button>
          </form>
        </div>

        {/* Footer */}
        <p className="text-center text-xs text-text-muted mt-6">
          Edu-LLM v3 Lean MVP — Polytech AI
        </p>
      </div>
    </div>
  );
}
