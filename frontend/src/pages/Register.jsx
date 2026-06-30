/**
 * Register.jsx — Student self-registration page for Edu-LLM v7.2.
 */

import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';

export default function Register() {
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
      setError('Passwords do not match');
      return;
    }
    if (password.length < 6) {
      setError('Password must be at least 6 characters');
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
        throw new Error(body.detail || 'Registration failed');
      }

      toast.success('Account created! Please sign in.');
      navigate('/login', { replace: true });
    } catch (err) {
      setError(err.message || 'Registration failed');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-ink-deep relative overflow-hidden">
      {/* Background glow */}
      <div className="absolute top-[-20%] right-[-10%] w-[500px] h-[500px] rounded-full bg-cyan/[0.04] blur-[120px] pointer-events-none" />

      <div className="relative z-10 w-full max-w-md px-4 animate-fade-in">
        <div className="bg-ink-base rounded-2xl border border-border-subtle shadow-elevated overflow-hidden noise">
          <div className="relative z-10 px-8 pt-10 pb-6 text-center">
            <div className="mx-auto w-14 h-14 rounded-xl gradient-cyan flex items-center justify-center shadow-glow mb-5">
              <svg className="w-7 h-7 text-cream" viewBox="0 0 24 24" fill="currentColor">
                <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M12 7a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM20 8v6M23 11l-3 3-3-3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" fill="none" />
              </svg>
            </div>
            <h1 className="text-3xl font-display text-cream mb-1.5 tracking-tight">Create Account</h1>
            <p className="text-sm text-cream-secondary">Register as a student to join classes</p>
          </div>

          <form onSubmit={handleSubmit} className="relative z-10 px-8 pb-10 space-y-5">
            {error && (
              <div className="flex items-center gap-2.5 px-4 py-3 rounded-lg bg-danger-muted border border-danger/20 text-danger text-sm animate-fade-in">
                {error}
              </div>
            )}

            <div className="space-y-1.5">
              <label htmlFor="reg-username" className="mono-label">Username</label>
              <input id="reg-username" type="text" required autoComplete="username"
                value={username} onChange={(e) => setUsername(e.target.value)}
                placeholder="Choose a username"
                className="w-full px-4 py-3 rounded-lg bg-ink-deep border border-border-default text-cream text-sm placeholder:text-cream-muted focus:outline-none focus:border-cyan focus:ring-1 focus:ring-cyan/30 transition-colors" />
            </div>

            <div className="space-y-1.5">
              <label htmlFor="reg-password" className="mono-label">Password</label>
              <input id="reg-password" type="password" required autoComplete="new-password"
                value={password} onChange={(e) => setPassword(e.target.value)}
                placeholder="At least 6 characters"
                className="w-full px-4 py-3 rounded-lg bg-ink-deep border border-border-default text-cream text-sm placeholder:text-cream-muted focus:outline-none focus:border-cyan focus:ring-1 focus:ring-cyan/30 transition-colors" />
            </div>

            <div className="space-y-1.5">
              <label htmlFor="reg-confirm" className="mono-label">Confirm Password</label>
              <input id="reg-confirm" type="password" required autoComplete="new-password"
                value={confirm} onChange={(e) => setConfirm(e.target.value)}
                placeholder="Repeat your password"
                className="w-full px-4 py-3 rounded-lg bg-ink-deep border border-border-default text-cream text-sm placeholder:text-cream-muted focus:outline-none focus:border-cyan focus:ring-1 focus:ring-cyan/30 transition-colors" />
            </div>

            <button id="register-submit" type="submit" disabled={isLoading}
              className="w-full py-3 rounded-lg gradient-cyan text-cream text-sm font-semibold tracking-wide transition-all duration-200 hover:brightness-110 active:scale-[0.98] disabled:opacity-60 disabled:cursor-not-allowed cursor-pointer shadow-glow">
              {isLoading ? (
                <span className="flex items-center justify-center gap-2">
                  <div className="w-4 h-4 rounded-full border-2 border-cream/30 border-t-cream animate-[spin_0.8s_linear_infinite]" />
                  Creating account…
                </span>
              ) : 'Create Account'}
            </button>

            <p className="text-center text-xs text-cream-muted">
              Already have an account?{' '}
              <Link to="/login" className="text-cyan hover:underline">Sign in</Link>
            </p>
          </form>
        </div>
      </div>
    </div>
  );
}
