/**
 * api.js — Lightweight fetch wrapper with JWT auth for Edu-LLM v3.
 *
 * Automatically attaches Authorization header from the auth store.
 * Redirects to /login on 401 responses.
 */

import useAuthStore from '../store/authStore';

const BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

/**
 * Core fetch wrapper.  All methods go through here.
 */
async function request(endpoint, options = {}) {
  const token = useAuthStore.getState().token;
  const headers = {
    'Content-Type': 'application/json',
    ...options.headers,
  };

  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch(`${BASE_URL}${endpoint}`, {
    ...options,
    headers,
  });

  // Auto-logout on 401
  if (res.status === 401) {
    useAuthStore.getState().logout();
    window.location.href = '/login';
    throw new Error('Unauthorized');
  }

  return res;
}

/**
 * Parse JSON response, throwing on non-OK status.
 */
async function jsonRequest(endpoint, options = {}) {
  const res = await request(endpoint, options);

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      // response may not be JSON
    }
    const error = new Error(detail);
    error.status = res.status;
    throw error;
  }

  // 204 No Content
  if (res.status === 204) return null;

  return res.json();
}

const api = {
  get: (endpoint) => jsonRequest(endpoint, { method: 'GET' }),

  post: (endpoint, body) =>
    jsonRequest(endpoint, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  put: (endpoint, body) =>
    jsonRequest(endpoint, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  delete: (endpoint) => jsonRequest(endpoint, { method: 'DELETE' }),

  /**
   * Raw fetch for non-JSON responses (e.g. SSE streams).
   * Does NOT parse JSON — returns the raw Response object.
   */
  raw: (endpoint, options = {}) => request(endpoint, options),
};

export default api;
