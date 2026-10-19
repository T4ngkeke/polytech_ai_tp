/**
 * main.jsx — React entry point for Edu-LLM.
 *
 * Boots up:
 *   1. Auth store hydration (restores JWT from localStorage)
 *   2. BrowserRouter
 *   3. Toast notifications (react-hot-toast) in the theme colors
 */

import { StrictMode, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import toast, { Toaster } from 'react-hot-toast';

import App from './App';
import useAuthStore, { takeTabNote } from './store/authStore';
import { applyTheme, storedTheme } from './lib/theme';
import './index.css';

applyTheme(storedTheme());

function Root() {
  const hydrate = useAuthStore((s) => s.hydrate);
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const followOtherTabs = useAuthStore((s) => s.followOtherTabs);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  useEffect(() => followOtherTabs(), [followOtherTabs]);

  useEffect(() => {
    const note = takeTabNote();
    if (note) toast(note, { duration: 5000 });
  }, []);

  if (!isHydrated) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-ink-deep">
        <div className="flex flex-col items-center gap-4 animate-fade-in">
          <div className="w-10 h-10 rounded-full border-2 border-cyan border-t-transparent animate-[spin_0.8s_linear_infinite]" />
          <span className="text-sm text-cream-muted font-medium">Loading…</span>
        </div>
      </div>
    );
  }

  return <App />;
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter>
      <Root />
      <Toaster
        position="top-right"
        toastOptions={{
          duration: 3000,
          style: {
            background: 'rgb(var(--ink-raised))',
            color: 'rgb(var(--cream))',
            border: '1px solid rgb(var(--line) / var(--line-default))',
            borderRadius: '10px',
            fontSize: '13px',
            fontFamily: 'var(--font-body)',
          },
          success: {
            iconTheme: { primary: 'rgb(var(--success))', secondary: 'rgb(var(--ink-raised))' },
          },
          error: {
            iconTheme: { primary: 'rgb(var(--danger))', secondary: 'rgb(var(--ink-raised))' },
          },
        }}
      />
    </BrowserRouter>
  </StrictMode>
);
