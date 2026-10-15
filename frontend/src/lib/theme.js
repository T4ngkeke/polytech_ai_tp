/**
 * Light or dark theme. Light is the default; the choice is remembered in this
 * browser only. The theme is the `data-theme` attribute of <html>; the colors
 * themselves are CSS variables in index.css.
 */

import { useSyncExternalStore } from 'react';

const KEY = 'edullm-theme';
const listeners = new Set();

export function storedTheme(getStorage = () => localStorage) {
  try {
    return getStorage().getItem(KEY) === 'dark' ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

export function currentTheme() {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  listeners.forEach((fn) => fn());
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
