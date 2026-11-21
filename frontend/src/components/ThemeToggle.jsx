/**
 * Switches between the light and dark theme (see lib/theme.js).
 */

import { toggleTheme, useTheme } from '../lib/theme';
import { useT } from '../lib/i18n';

export default function ThemeToggle({ className = '' }) {
  const t = useT();
  const theme = useTheme();
  const label = theme === 'dark' ? t('theme.light') : t('theme.dark');
  return (
    <button type="button" onClick={toggleTheme} title={label} aria-label={label}
      className={`cursor-pointer rounded-full p-2 text-cream-secondary transition-colors hover:bg-ink-hover ${className}`}>
      {theme === 'dark' ? <SunIcon className="h-4 w-4" /> : <MoonIcon className="h-4 w-4" />}
    </button>
  );
}

function SunIcon({ className }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="4" />
      <path strokeLinecap="round" d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41m11.32-11.32 1.41-1.41" />
    </svg>
  );
}

function MoonIcon({ className }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}
