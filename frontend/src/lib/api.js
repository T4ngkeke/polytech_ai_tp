/**
 * api.js — Lightweight fetch wrapper with JWT auth for Edu-LLM.
 *
 * Automatically attaches Authorization header from the auth store.
 * Redirects to /login on 401 responses.
 *
 * Generic verbs (get/post/put/delete/upload/raw); endpoints live at call sites.
 * Note: student DELETE /api/student/sessions/{id} is a soft delete (the session
 * is hidden from the student but retained for teacher/admin audit).
 */

import useAuthStore from '../store/authStore';

const BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

async function request(endpoint, options = {}) {
  const token = useAuthStore.getState().token;
  const headers = { ...options.headers };

  if (!(options.body instanceof FormData)) {
    headers['Content-Type'] = headers['Content-Type'] || 'application/json';
  }

  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch(`${BASE_URL}${endpoint}`, { ...options, headers });

  if (res.status === 401) {
    useAuthStore.getState().logout();
    window.location.href = '/login';
    throw new Error('Unauthorized');
  }

  return res;
}

async function jsonRequest(endpoint, options = {}) {
  const res = await request(endpoint, options);

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { const body = await res.json(); detail = body.detail || detail; } catch {}
    const error = new Error(detail);
    error.status = res.status;
    throw error;
  }

  if (res.status === 204) return null;
  return res.json();
}

const api = {
  get: (endpoint) => jsonRequest(endpoint, { method: 'GET' }),

  post: (endpoint, body) =>
    jsonRequest(endpoint, { method: 'POST', body: JSON.stringify(body) }),

  put: (endpoint, body) =>
    jsonRequest(endpoint, { method: 'PUT', body: JSON.stringify(body) }),

  delete: (endpoint) => jsonRequest(endpoint, { method: 'DELETE' }),

  /**
   * Upload a file via multipart/form-data (e.g. CSV import).
   * Pass a FormData instance as the body.
   */
  upload: (endpoint, formData, queryParams = '') =>
    jsonRequest(`${endpoint}${queryParams}`, { method: 'POST', body: formData }),

  /**
   * Raw fetch for non-JSON responses (SSE streams).
   */
  raw: (endpoint, options = {}) => request(endpoint, options),
};

export default api;
