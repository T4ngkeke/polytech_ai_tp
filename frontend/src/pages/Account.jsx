/**
 * Account.jsx — [v7.2] User center.
 *
 * Self-service password change for every role (student / teacher / admin).
 * The old password is verified server-side; on success the user stays logged in.
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';

import api from '../lib/api';
import useAuthStore from '../store/authStore';

export default function Account() {
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
      toast.success('Password changed');
      setOldPassword('');
      setNewPassword('');
      setConfirm('');
    } catch (err) {
      toast.error(err.message || 'Could not change password');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col h-full bg-ink-deep overflow-y-auto">
      <div className="max-w-md w-full mx-auto p-8 space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold text-cream">Account</h1>
          <button
            onClick={() => navigate(-1)}
            className="text-xs text-cream-muted hover:text-cream cursor-pointer"
          >
            ← Back
          </button>
        </div>

        <div className="rounded-xl border border-border-default bg-ink-base p-4">
          <p className="text-sm font-semibold text-cream truncate">{username || 'User'}</p>
          <p className="text-[10px] uppercase tracking-wider text-cyan">{userRole}</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <h2 className="text-sm font-semibold text-cream">Change password</h2>

          <PasswordField label="Current password" value={oldPassword} onChange={setOldPassword} autoComplete="current-password" />
          <PasswordField label="New password" value={newPassword} onChange={setNewPassword} autoComplete="new-password" />
          {tooShort && <p className="text-xs text-danger">Must be at least 6 characters.</p>}
          <PasswordField label="Confirm new password" value={confirm} onChange={setConfirm} autoComplete="new-password" />
          {mismatch && <p className="text-xs text-danger">Passwords do not match.</p>}

          <button
            type="submit"
            disabled={!canSubmit}
            className="w-full py-3 rounded-lg gradient-cyan text-cream text-sm font-semibold hover:brightness-110 transition-all cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {saving ? 'Saving…' : 'Change password'}
          </button>
        </form>
      </div>
    </div>
  );
}

function PasswordField({ label, value, onChange, autoComplete }) {
  return (
    <div className="space-y-1.5">
      <label className="mono-label">{label}</label>
      <input
        type="password"
        value={value}
        autoComplete={autoComplete}
        onChange={(e) => onChange(e.target.value)}
        className="w-full px-4 py-3 rounded-lg bg-ink-deep border border-border-default text-cream text-sm placeholder:text-cream-muted focus:outline-none focus:border-cyan/40 font-mono transition-colors"
      />
    </div>
  );
}
