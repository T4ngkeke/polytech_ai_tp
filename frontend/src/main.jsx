/**
 * main.jsx — React entry point for Edu-LLM v5.
 *
 * Boots up:
 *   1. Auth store hydration (restores JWT from localStorage)
 *   2. BrowserRouter
 *   3. Toast notifications (react-hot-toast) — Dossier Noir themed
 */

import { StrictMode, useEffect } from 'react';
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
            background: '#1a1d25',
            color: '#e8e2d6',
            border: '1px solid rgba(232, 226, 214, 0.1)',
            borderRadius: '10px',
            fontSize: '13px',
            fontFamily: '"Geist", system-ui, sans-serif',
          },
          success: {
            iconTheme: { primary: '#3ecf71', secondary: '#e8e2d6' },
          },
          error: {
            iconTheme: { primary: '#ff2d2d', secondary: '#e8e2d6' },
          },
        }}
      />
    </BrowserRouter>
  </StrictMode>
);
