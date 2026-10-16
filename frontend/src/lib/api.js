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

/**
 * Error for a failed response. `detail` is either a string or
 * `{ code, message }`; the machine-readable code is kept on `error.code`.
 */
export async function responseError(res) {
  let message = `HTTP ${res.status}`;
  let code;
  try {
    const { detail } = await res.json();
    if (typeof detail === 'string') message = detail;
    else if (detail && typeof detail.message === 'string') ({ message, code } = detail);
  } catch { /* not JSON */ }
  const error = new Error(message);
  error.status = res.status;
  error.code = code;
  return error;
}

async function jsonRequest(endpoint, options = {}) {
  const res = await request(endpoint, options);

  if (!res.ok) throw await responseError(res);

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
   * Upload with onProgress(share 0..1) while the file is sent. fetch cannot
   * report upload progress, so this one uses XMLHttpRequest.
   */
  uploadWithProgress: (endpoint, formData, onProgress) => new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${BASE_URL}${endpoint}`);
    const token = useAuthStore.getState().token;
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress?.(e.loaded / e.total); };
    xhr.onerror = () => reject(new Error('Network error: the file was not sent'));
    xhr.onload = async () => {
      if (xhr.status === 401) {
        useAuthStore.getState().logout();
        window.location.href = '/login';
        reject(new Error('Unauthorized'));
        return;
      }
      const res = new Response(xhr.responseText || null, { status: xhr.status });
      if (!res.ok) reject(await responseError(res));
      else resolve(xhr.status === 204 ? null : JSON.parse(xhr.responseText));
    };
    xhr.send(formData);
  }),

  /**
   * Raw fetch for non-JSON responses (SSE streams).
   */
  raw: (endpoint, options = {}) => request(endpoint, options),
};

export default api;
