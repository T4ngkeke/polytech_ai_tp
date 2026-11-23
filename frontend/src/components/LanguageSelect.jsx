/**
 * LanguageSelect.jsx — the page language picker (users.ui_language).
 *
 * The same for every role: students have it in Settings › General, teachers
 * and admins on the account page. "Automatic" follows the browser.
 */
import toast from 'react-hot-toast';
import useAuthStore from '../store/authStore';
import { LANGUAGES, useLanguage, useT } from '../lib/i18n';

export default function LanguageSelect({ id = 'settings-language' }) {
  const t = useT();
  const lang = useLanguage();
  const setting = useAuthStore((s) => s.uiLanguage);
  const saveSettings = useAuthStore((s) => s.saveSettings);
  return (
    <select id={id} value={setting || ''} aria-label={t('settings.language')}
      onChange={(e) => saveSettings({ ui_language: e.target.value || null }).catch(() => toast.error(t('error.save')))}
      className="rounded-lg border border-border-default bg-ink-raised px-2.5 py-1.5 text-[13.5px] text-cream">
      <option value="">{t('settings.languageAuto', { lang: LANGUAGES.find((l) => l.code === lang)?.label })}</option>
      {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
    </select>
  );
}
