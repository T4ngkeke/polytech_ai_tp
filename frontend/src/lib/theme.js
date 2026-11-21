/**
 * Light, dark, or the system's theme. Light is the default; the choice is
 * remembered in this browser only. The theme is the `data-theme` attribute of
 * <html> ('light' or 'dark'; none follows the system); the colors themselves
 * are CSS variables in index.css.
 */

import { useSyncExternalStore } from 'react';

const KEY = 'edullm-theme';
const listeners = new Set();

export function storedTheme(getStorage = () => localStorage) {
  try {
    const value = getStorage().getItem(KEY);
    return value === 'dark' || value === 'system' ? value : 'light';
  } catch {
    return 'light';
  }
}

const systemDark = () => typeof window !== 'undefined' && !!window.matchMedia
  && window.matchMedia('(prefers-color-scheme: dark)').matches;

/** The theme shown now: 'light' or 'dark' (the system's when following it). */
export function currentTheme() {
  const set = document.documentElement.dataset.theme;
  if (set === 'dark' || set === 'light') return set;
  return systemDark() ? 'dark' : 'light';
}

/** The choice: 'light', 'dark' or 'system'. */
export function themeChoice() {
  const set = document.documentElement.dataset.theme;
  return set === 'dark' || set === 'light' ? set : 'system';
}

export function applyTheme(theme) {
  if (theme === 'system') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  listeners.forEach((fn) => fn());
}

if (typeof window !== 'undefined' && window.matchMedia) {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change',
    () => listeners.forEach((fn) => fn()));
}

export function setTheme(theme) {
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // Private window or blocked storage: the choice lasts until reload.
  }
  applyTheme(theme);
}

export function toggleTheme() {
  setTheme(currentTheme() === 'dark' ? 'light' : 'dark');
}

function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function useTheme() {
  return useSyncExternalStore(subscribe, currentTheme);
}
