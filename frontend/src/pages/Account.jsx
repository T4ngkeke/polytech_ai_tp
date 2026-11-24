/**
 * Account.jsx — User center.
 *
 * Page language and self-service password change for every role (student /
 * teacher / admin).
 * The old password is verified server-side; on success the user stays logged in.
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';

import api from '../lib/api';
import useAuthStore from '../store/authStore';
import { useT } from '../lib/i18n';
import PasswordInput from '../components/PasswordInput';
import LanguageSelect from '../components/LanguageSelect';

export default function Account() {
  const t = useT();
  const navigate = useNavigate();
  const { username, userRole } = useAuthStore();

  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [saving, setSaving] = useState(false);

  const tooShort = newPassword.length > 0 && newPassword.length < 6;
  const mismatch = confirm.length > 0 && newPassword !== confirm;
  const canSubmit =
    oldPassword.length > 0 && newPassword.length >= 6 && newPassword === confirm && !saving;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!canSubmit) return;
    setSaving(true);
    try {
      await api.post('/api/auth/change-password', {
        old_password: oldPassword,
        new_password: newPassword,
      });
      toast.success(t('password.changed'));
      setOldPassword('');
      setNewPassword('');
      setConfirm('');
    } catch (err) {
      toast.error(err.message || t('p.account.failed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-ink-deep">
      <div className="mx-auto w-full max-w-md space-y-5 p-5 sm:p-8">
        <div className="flex items-center justify-between">
          <h1 className="text-[18px] font-semibold text-cream">{t('settings.tab.account')}</h1>
          <button
            onClick={() => navigate(-1)}
            className="cursor-pointer rounded-full px-3 py-1 text-[13px] text-cream-secondary hover:bg-ink-hover"
          >
            ← {t('back')}
          </button>
        </div>

        <div className="rounded-2xl border border-border-subtle bg-ink-raised p-5">
          <p className="truncate text-[15px] font-semibold text-cream">{username || t('p.account.user')}</p>
          <p className="text-[13px] text-cream-muted">{userRole && t(`a.role.${userRole}`)}</p>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border-subtle bg-ink-raised p-5">
          <label htmlFor="account-language" className="text-[15px] font-semibold text-cream">{t('settings.language')}</label>
          <LanguageSelect id="account-language" />
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 rounded-2xl border border-border-subtle bg-ink-raised p-5">
          <h2 className="text-[15px] font-semibold text-cream">{t('password.change')}</h2>

          <PasswordField label={t('password.old')} value={oldPassword} onChange={setOldPassword} autoComplete="current-password" />
          <PasswordField label={t('password.new')} value={newPassword} onChange={setNewPassword} autoComplete="new-password" />
          {tooShort && <p className="text-[13px] text-danger">{t('password.short')}</p>}
          <PasswordField label={t('password.again')} value={confirm} onChange={setConfirm} autoComplete="new-password" />
          {mismatch && <p className="text-[13px] text-danger">{t('password.mismatch')}</p>}

          <button
            type="submit"
            disabled={!canSubmit}
            className="w-full cursor-pointer rounded-full bg-ink px-4 py-2 text-[14px] text-on-ink transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {saving ? t('p.choose.saving') : t('password.change')}
          </button>
        </form>
      </div>
    </div>
  );
}

function PasswordField({ label, value, onChange, autoComplete }) {
  return (
    <div className="space-y-1.5">
      <label className="block text-[13px] text-cream-muted">
        {label}
        <PasswordInput
          wrapperClassName="mt-1.5"
          value={value}
          autoComplete={autoComplete}
          onChange={(e) => onChange(e.target.value)}
          className="w-full rounded-xl border border-border-default bg-ink-raised px-3 py-2 text-[14px] text-cream focus:border-cream-secondary focus:outline-none"
        />
      </label>
    </div>
  );
}
