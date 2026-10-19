/**
 * authStore.js — Zustand global auth state for Edu-LLM.
 *
 * Stores: token, userId, username, userRole
 * Persists JWT to localStorage; hydrates on app boot.
 *
 * Every tab of the browser shares the one token in localStorage. When another
 * tab signs in as someone else or signs out, followOtherTabs() reloads this
 * tab on the new account's page (or the login page), so a tab never shows one
 * account while its requests go out as another.
 */

import { create } from 'zustand';

const TOKEN_KEY = 'edu_llm_token';
// Set before a reload caused by another tab; read once on boot (takeTabNote).
const TAB_NOTE_KEY = 'edu_llm_tab_note';

/** Role → default landing route */
const DEFAULT_ROUTES = {
  admin: '/admin',
  teacher: '/teacher',
  student: '/chat',
};

const useAuthStore = create((set, get) => ({
  // ── State ──
  token: null,
  userId: null,
  username: null,
  userRole: null,
  isHydrated: false,

  // ── Derived ──
  isAuthenticated: () => !!get().token,
  getDefaultRoute: () => DEFAULT_ROUTES[get().userRole] || '/chat',

  // ── Actions ──

  /**
   * After obtaining a token, call /api/auth/me to fetch user profile
   * and populate the store.
   */
  login: async (accessToken) => {
    localStorage.setItem(TOKEN_KEY, accessToken);
    set({ token: accessToken });

    try {
      const res = await fetch('/api/auth/me', {
        headers: { Authorization: `Bearer ${accessToken}` },
      });

      if (!res.ok) {
        // Token is invalid — clear everything
        get().logout();
        throw new Error('Failed to fetch user profile');
      }

      const user = await res.json();
      set({
        userId: user.id,
        username: user.username,
        userRole: user.role,
      });

      return user.role;
    } catch (err) {
      get().logout();
      throw err;
    }
  },

  /**
   * Clear all auth state and localStorage.
   */
  logout: () => {
    localStorage.removeItem(TOKEN_KEY);
    set({
      token: null,
      userId: null,
      username: null,
      userRole: null,
    });
  },

  /**
   * On app boot, check localStorage for an existing token
   * and re-fetch user profile if found.
   */
  hydrate: async () => {
    const storedToken = localStorage.getItem(TOKEN_KEY);
    if (!storedToken) {
      set({ isHydrated: true });
      return;
    }

    set({ token: storedToken });

    try {
      const res = await fetch('/api/auth/me', {
        headers: { Authorization: `Bearer ${storedToken}` },
      });

      if (!res.ok) {
        get().logout();
        set({ isHydrated: true });
        return;
      }

      const user = await res.json();
      set({
        userId: user.id,
        username: user.username,
        userRole: user.role,
        isHydrated: true,
      });
    } catch {
      get().logout();
      set({ isHydrated: true });
    }
  },

  /**
   * Follow sign-ins and sign-outs made in other tabs. Returns the unsubscribe.
   */
  followOtherTabs: () => {
    const onStorage = async (event) => {
      if (event.key !== TOKEN_KEY && event.key !== null) return;
      const token = localStorage.getItem(TOKEN_KEY);
      if (token === get().token) return;
      if (!token) {
        set({ token: null, userId: null, username: null, userRole: null });
        noteForReload('Signed out in another tab.');
        window.location.replace('/login');
        return;
      }
      const res = await fetch('/api/auth/me', { headers: { Authorization: `Bearer ${token}` } }).catch(() => null);
      if (!res?.ok) return;
      const user = await res.json();
      noteForReload(`Signed in as ${user.username} in another tab.`);
      window.location.replace(DEFAULT_ROUTES[user.role] || '/login');
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  },
}));

function noteForReload(text) {
  try { sessionStorage.setItem(TAB_NOTE_KEY, text); } catch { /* no storage: no note */ }
}

/** The note left by a reload that another tab caused, once; null otherwise. */
export function takeTabNote() {
  try {
    const text = sessionStorage.getItem(TAB_NOTE_KEY);
    sessionStorage.removeItem(TAB_NOTE_KEY);
    return text;
  } catch {
    return null;
  }
}

export { DEFAULT_ROUTES };
export default useAuthStore;
