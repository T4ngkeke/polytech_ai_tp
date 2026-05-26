/**
 * Admin.jsx — Admin user management page for Edu-LLM v3.
 *
 * Features:
 *   - User table from GET /api/admin/users
 *   - Inline quota editing (PUT on blur/Enter)
 *   - Soft-delete with confirmation modal
 */

import { useState, useEffect, useRef } from 'react';
import toast from 'react-hot-toast';
import api from '../lib/api';

const ROLE_BADGES = {
  admin: { bg: 'bg-danger-muted', text: 'text-danger' },
  teacher: { bg: 'bg-accent-warm-muted', text: 'text-accent-warm' },
  student: { bg: 'bg-accent-muted', text: 'text-accent' },
};

export default function Admin() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [deleteTarget, setDeleteTarget] = useState(null);

  useEffect(() => {
    loadUsers();
  }, []);

  async function loadUsers() {
    setLoading(true);
    try {
      const data = await api.get('/api/admin/users');
      setUsers(data);
    } catch {
      toast.error('Failed to load users');
    } finally {
      setLoading(false);
    }
  }

  async function handleQuotaUpdate(userId, newQuota) {
    try {
      const updated = await api.put(`/api/admin/users/${userId}/quota`, {
        daily_token_quota: newQuota,
      });
      setUsers((prev) =>
        prev.map((u) => (u.id === userId ? { ...u, daily_token_quota: updated.daily_token_quota } : u))
      );
      toast.success('Quota updated');
    } catch (err) {
      toast.error(err.message || 'Failed to update quota');
    }
  }

  async function handleDelete(userId) {
    try {
      await api.delete(`/api/admin/users/${userId}`);
      setUsers((prev) => prev.filter((u) => u.id !== userId));
      toast.success('User deleted');
    } catch (err) {
      toast.error(err.message || 'Failed to delete user');
    } finally {
      setDeleteTarget(null);
    }
  }

  return (
    <div className="h-[calc(100vh-var(--header-height))] overflow-y-auto">
      <div className="p-6 animate-fade-in">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-xl font-bold font-[var(--font-display)] text-text-primary">
              User Management
            </h2>
            <p className="text-sm text-text-secondary mt-1">
              Manage platform users, roles, and daily token quotas
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className="px-3 py-1.5 rounded-lg bg-bg-surface border border-border-subtle text-xs text-text-secondary">
              {users.length} user{users.length !== 1 ? 's' : ''}
            </span>
            <button
              onClick={loadUsers}
              className="px-3 py-1.5 rounded-lg text-xs font-medium text-text-secondary hover:text-accent hover:bg-accent-muted transition-colors cursor-pointer"
            >
              ↻ Refresh
            </button>
          </div>
        </div>

        {/* Table */}
        {loading ? (
          <div className="flex items-center justify-center py-20">
            <div className="w-6 h-6 rounded-full border-2 border-accent border-t-transparent animate-[spin_0.8s_linear_infinite]" />
          </div>
        ) : users.length === 0 ? (
          <div className="text-center py-20 text-sm text-text-muted">
            No users found.
          </div>
        ) : (
          <div className="rounded-xl border border-border-subtle overflow-hidden shadow-[var(--shadow-card)]">
            <table className="w-full">
              <thead>
                <tr className="bg-bg-elevated/60">
                  <th className="text-left px-5 py-3.5 text-xs font-semibold uppercase tracking-wider text-text-muted">User</th>
                  <th className="text-center px-5 py-3.5 text-xs font-semibold uppercase tracking-wider text-text-muted">Role</th>
                  <th className="text-right px-5 py-3.5 text-xs font-semibold uppercase tracking-wider text-text-muted">Daily Token Quota</th>
                  <th className="text-center px-5 py-3.5 text-xs font-semibold uppercase tracking-wider text-text-muted">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {users.map((user) => (
                  <UserRow
                    key={user.id}
                    user={user}
                    onQuotaUpdate={handleQuotaUpdate}
                    onDeleteClick={() => setDeleteTarget(user)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Delete Confirmation Modal */}
      {deleteTarget && (
        <DeleteConfirmModal
          user={deleteTarget}
          onConfirm={() => handleDelete(deleteTarget.id)}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════
   USER TABLE ROW
   ═══════════════════════════════════════════ */

function UserRow({ user, onQuotaUpdate, onDeleteClick }) {
  const [editingQuota, setEditingQuota] = useState(false);
  const [quotaValue, setQuotaValue] = useState(String(user.daily_token_quota));
  const inputRef = useRef(null);

  const badge = ROLE_BADGES[user.role] || ROLE_BADGES.student;

  useEffect(() => {
    setQuotaValue(String(user.daily_token_quota));
  }, [user.daily_token_quota]);

  useEffect(() => {
    if (editingQuota) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editingQuota]);

  const commitQuota = () => {
    setEditingQuota(false);
    const parsed = parseInt(quotaValue, 10);
    if (isNaN(parsed) || parsed < 0) {
      setQuotaValue(String(user.daily_token_quota));
      return;
    }
    if (parsed !== user.daily_token_quota) {
      onQuotaUpdate(user.id, parsed);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      commitQuota();
    } else if (e.key === 'Escape') {
      setEditingQuota(false);
      setQuotaValue(String(user.daily_token_quota));
    }
  };

  return (
    <tr className="hover:bg-bg-surface-hover/50 transition-colors group">
      {/* User info */}
      <td className="px-5 py-3.5">
        <div className="flex items-center gap-3">
          <div className={`w-9 h-9 rounded-full flex items-center justify-center text-xs font-bold uppercase ${badge.bg} ${badge.text}`}>
            {user.username.charAt(0)}
          </div>
          <div>
            <div className="text-sm font-medium text-text-primary">{user.username}</div>
            <div className="text-[11px] text-text-muted font-mono">{user.id.slice(0, 8)}…</div>
          </div>
        </div>
      </td>

      {/* Role */}
      <td className="px-5 py-3.5 text-center">
        <span className={`inline-block px-2.5 py-1 rounded-full text-[11px] font-semibold uppercase tracking-wider ${badge.bg} ${badge.text}`}>
          {user.role}
        </span>
      </td>

      {/* Quota (inline edit) */}
      <td className="px-5 py-3.5 text-right">
        {editingQuota ? (
          <input
            ref={inputRef}
            type="number"
            min="0"
            value={quotaValue}
            onChange={(e) => setQuotaValue(e.target.value)}
            onBlur={commitQuota}
            onKeyDown={handleKeyDown}
            className="w-28 ml-auto px-3 py-1.5 rounded-lg bg-bg-primary border border-accent text-sm text-text-primary text-right focus:outline-none focus:ring-1 focus:ring-accent/50"
          />
        ) : (
          <button
            onClick={() => setEditingQuota(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm text-text-secondary hover:text-text-primary hover:bg-bg-surface-hover transition-colors cursor-pointer group/quota"
            title="Click to edit"
          >
            {user.daily_token_quota.toLocaleString()}
            <EditIcon className="w-3 h-3 opacity-0 group-hover/quota:opacity-60 transition-opacity" />
          </button>
        )}
      </td>

      {/* Actions */}
      <td className="px-5 py-3.5 text-center">
        <button
          id={`delete-user-${user.id}`}
          onClick={onDeleteClick}
          className="px-3 py-1.5 rounded-lg text-xs font-medium text-danger bg-danger-muted hover:bg-danger/20 transition-colors cursor-pointer opacity-0 group-hover:opacity-100"
        >
          Delete
        </button>
      </td>
    </tr>
  );
}

/* ═══════════════════════════════════════════
   DELETE CONFIRMATION MODAL
   ═══════════════════════════════════════════ */

function DeleteConfirmModal({ user, onConfirm, onCancel }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onCancel} />

      {/* Modal */}
      <div className="relative w-full max-w-sm bg-bg-surface rounded-2xl border border-border-subtle shadow-[var(--shadow-elevated)] animate-fade-in p-6">
        {/* Warning icon */}
        <div className="mx-auto w-12 h-12 rounded-full bg-danger-muted flex items-center justify-center mb-4">
          <WarningIcon className="w-6 h-6 text-danger" />
        </div>

        <h3 className="text-lg font-bold font-[var(--font-display)] text-text-primary text-center mb-2">
          Delete User?
        </h3>
        <p className="text-sm text-text-secondary text-center mb-6">
          Are you sure you want to delete <strong className="text-text-primary">{user.username}</strong>?
          This will soft-delete their account.
        </p>

        <div className="flex gap-3">
          <button
            onClick={onCancel}
            className="flex-1 px-4 py-2.5 rounded-lg border border-border-default text-sm font-medium text-text-secondary hover:text-text-primary hover:bg-bg-surface-hover transition-colors cursor-pointer"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="flex-1 px-4 py-2.5 rounded-lg bg-danger text-white text-sm font-semibold hover:bg-danger-hover transition-colors cursor-pointer"
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── Icons ── */

function EditIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
  );
}

function WarningIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
      <line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  );
}
