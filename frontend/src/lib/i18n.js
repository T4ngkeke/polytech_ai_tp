/**
 * i18n.js — the interface text in Chinese, French and English.
 *
 * Only the interface goes through t(): conversation content, course files and
 * what teachers write stay as they are. The language is the user's setting
 * (users.ui_language, Settings › General), else the browser's, else French
 * (the students' language). Strings live in i18n.strings.js as
 * { key: { zh, fr, en } }; "{name}" parts are filled from the second argument.
 */
import { useCallback } from 'react';
import useAuthStore from '../store/authStore';
import CORE from './i18n.strings.js';
import TEACHER from './i18n.teacher.js';
import PAGES from './i18n.pages.js';
import ADMIN from './i18n.admin.js';

// Student chat and shared text, the teacher's pages, the other pages (login, files…).
const STRINGS = { ...ADMIN, ...PAGES, ...TEACHER, ...CORE };

export const LANGUAGES = [
  { code: 'zh', label: '中文' },
  { code: 'fr', label: 'Français' },
  { code: 'en', label: 'English' },
];

/** The browser's language among ours (fr when none fits). */
export function browserLanguage(languages = (typeof navigator !== 'undefined' && navigator.languages) || []) {
  for (const tag of languages) {
    const code = String(tag).slice(0, 2).toLowerCase();
    if (code === 'zh' || code === 'fr' || code === 'en') return code;
  }
  return 'fr';
}

export function resolveLanguage(setting) {
  return setting === 'zh' || setting === 'fr' || setting === 'en' ? setting : browserLanguage();
}

/** The text of `key` in `lang`, with {name} parts filled; the key itself when unknown. */
export function translate(lang, key, values) {
  const entry = STRINGS[key];
  let text = entry ? (entry[lang] ?? entry.fr ?? entry.en ?? key) : key;
  if (values) {
    text = text.replace(/\{(\w+)\}/g, (all, name) => (name in values ? String(values[name]) : all));
  }
  return text;
}

/** The language shown now (a hook). */
export function useLanguage() {
  return resolveLanguage(useAuthStore((s) => s.uiLanguage));
}

/** t(key, values) in the language shown now (a hook). */
export function useT() {
  const lang = useLanguage();
  return useCallback((key, values) => translate(lang, key, values), [lang]);
}

/** Outside React (toasts from plain functions). */
export function t(key, values) {
  return translate(resolveLanguage(useAuthStore.getState().uiLanguage), key, values);
}
