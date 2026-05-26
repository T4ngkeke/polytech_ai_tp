/**
 * authStore.js — Zustand global auth state for Edu-LLM v3.
 *
 * Stores: token, userId, username, userRole
 * Persists JWT to localStorage; hydrates on app boot.
 */

import { create } from 'zustand';

const TOKEN_KEY = 'edu_llm_token';

/** Role → default landing route */
const DEFAULT_ROUTES = {
  admin: '/admin/users',
  teacher: '/teacher/students',
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
}));

export { DEFAULT_ROUTES };
export default useAuthStore;
