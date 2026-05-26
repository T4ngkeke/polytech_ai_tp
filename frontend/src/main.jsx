/**
 * main.jsx — React entry point for Edu-LLM v3.
 *
 * Boots up:
 *   1. Auth store hydration (restores JWT from localStorage)
 *   2. BrowserRouter
 *   3. Toast notifications (react-hot-toast)
 */

import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { Toaster } from 'react-hot-toast';

import App from './App';
import useAuthStore from './store/authStore';
import './index.css';

function Root() {
  const hydrate = useAuthStore((s) => s.hydrate);
  const isHydrated = useAuthStore((s) => s.isHydrated);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  if (!isHydrated) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg-primary">
        <div className="flex flex-col items-center gap-4 animate-fade-in">
          <div className="w-10 h-10 rounded-full border-2 border-accent border-t-transparent animate-[spin_0.8s_linear_infinite]" />
          <span className="text-sm text-text-muted font-medium">Loading…</span>
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
            background: '#1a2332',
            color: '#f1f5f9',
            border: '1px solid rgba(148, 163, 184, 0.12)',
            borderRadius: '12px',
            fontSize: '13px',
            fontFamily: '"DM Sans", system-ui, sans-serif',
          },
          success: {
            iconTheme: { primary: '#10b981', secondary: '#fff' },
          },
          error: {
            iconTheme: { primary: '#ef4444', secondary: '#fff' },
          },
        }}
      />
    </BrowserRouter>
  </StrictMode>
);
