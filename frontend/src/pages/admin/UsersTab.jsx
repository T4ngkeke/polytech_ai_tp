/**
 * admin/UsersTab.jsx — Admin "Users" tab: list, roles, create, reset, delete.
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import useAuthStore from '../../store/authStore';
import Icon from '../../components/Icon';
import { classesByTeacher } from '../../lib/analytics';
import { plural } from '../../lib/plural';
import { PageHeader, SortableTh, IconButton, Spinner, Modal, ConfirmModal } from './shared';

/* ═══════════════════════════════ USERS TAB ═══════════════════════════════ */
const ROLE_FILTERS = ['all', 'student', 'teacher', 'admin'];
// The server's RESET_PASSWORD (backend config), shown to the admin.
const RESET_PASSWORD = '123456';

export default function UsersTab() {
  const [users, setUsers] = useState([]);
  const [owned, setOwned] = useState({});      // teacher id -> names of their classes
  const [loading, setLoading] = useState(true);
  const [showCsvModal, setShowCsvModal] = useState(false);
  const [csvFile, setCsvFile] = useState(null);
  const [csvPreview, setCsvPreview] = useState(null);
  const [importing, setImporting] = useState(false);
  const [resetUser, setResetUser] = useState(null);   // user being password-reset
  const [resetting, setResetting] = useState(false);
  const myId = useAuthStore((s) => s.userId);
  const [deleteUser, setDeleteUser] = useState(null);  // user pending delete confirm
  const [deleting, setDeleting] = useState(false);
  // table controls
  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState('all');
  const [sortKey, setSortKey] = useState('username');  // 'username' | 'daily_token_quota'
  const [sortDir, setSortDir] = useState('asc');

  const load = useCallback(async () => {
    try { setLoading(true); setUsers(await api.get('/api/admin/users')); }
    catch { toast.error('Failed to load users'); } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    api.get('/api/admin/classes').then((cs) => setOwned(classesByTeacher(cs))).catch(() => {});
  }, []);

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    let list = users.filter((u) =>
      (roleFilter === 'all' || u.role === roleFilter) &&
      (!q || u.username.toLowerCase().includes(q))
    );
    list = [...list].sort((a, b) => {
      let r;
      if (sortKey === 'daily_token_quota') r = (a.daily_token_quota || 0) - (b.daily_token_quota || 0);
      else r = a.username.localeCompare(b.username);
      return sortDir === 'asc' ? r : -r;
    });
    return list;
  }, [users, search, roleFilter, sortKey, sortDir]);

  const toggleSort = (key) => {
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortKey(key); setSortDir('asc'); }
  };

  const handleRoleChange = async (userId, role) => {
    try {
      const u = await api.put(`/api/admin/users/${userId}/role`, { role });
      setUsers((p) => p.map((x) => x.id === userId ? { ...x, role: u.role } : x));
    } catch (err) { toast.error(err.message); }
  };

  // The password becomes the reset one (123456); the user must change it at sign-in.
  const handleResetPassword = async () => {
    if (!resetUser) return;
    setResetting(true);
    try {
      const u = await api.put(`/api/admin/users/${resetUser.id}/password`);
      setUsers((p) => p.map((x) => x.id === u.id ? { ...x, must_change_password: u.must_change_password } : x));
      toast.success(`Password of ${resetUser.username} reset to ${RESET_PASSWORD}`);
      setResetUser(null);
    } catch (err) { toast.error(err.message); } finally { setResetting(false); }
  };

  // field: 'daily_token_quota' or 'weekly_token_quota' (the daily one is always sent).
  const handleQuotaChange = async (user, field, value) => {
    if (Number(value) === user[field]) return;
    try {
      const body = { daily_token_quota: user.daily_token_quota, [field]: Number(value) };
      const u = await api.put(`/api/admin/users/${user.id}/quota`, body);
      setUsers((p) => p.map((x) => x.id === user.id
        ? { ...x, daily_token_quota: u.daily_token_quota, weekly_token_quota: u.weekly_token_quota } : x));
    } catch (err) { toast.error(err.message); }
  };

  const handleRefill = async (user) => {
    try {
      const out = await api.post(`/api/admin/users/${user.id}/quota-refill`);
      toast.success(`${user.username}: ${(out.limit - out.used).toLocaleString()} tokens left today, ${(out.week_limit - out.week_used).toLocaleString()} this week`);
    } catch (err) { toast.error(err.message); }
  };

  const handleDelete = async () => {
    if (!deleteUser) return;
    setDeleting(true);
    try {
      await api.delete(`/api/admin/users/${deleteUser.id}`);
      setUsers((p) => p.filter((x) => x.id !== deleteUser.id));
      toast.success('User deleted');
      setDeleteUser(null);
    } catch (err) { toast.error(err.message); } finally { setDeleting(false); }
  };

  const handleCsvPreview = async () => {
    if (!csvFile) return;
    setImporting(true);
    try {
      const fd = new FormData(); fd.append('file', csvFile);
      const preview = await api.upload('/api/admin/users/import', fd, '?force=false');
      setCsvPreview(preview);
    } catch (err) { toast.error(err.message); } finally { setImporting(false); }
  };

  const handleCsvConfirm = async () => {
    if (!csvFile) return;
    setImporting(true);
    try {
      const fd = new FormData(); fd.append('file', csvFile);
      const result = await api.upload('/api/admin/users/import', fd, '?force=true');
      toast.success(`Imported: ${result.created} created, ${result.updated} updated`);
      setShowCsvModal(false); setCsvPreview(null); setCsvFile(null);
      load();
    } catch (err) { toast.error(err.message); } finally { setImporting(false); }
  };

  if (loading) return <Spinner />;

  return (
    <div className="p-4 md:p-8 overflow-y-auto h-full">
      <div className="max-w-5xl mx-auto">
        <PageHeader title="Users" subtitle={`${users.length} total accounts`}>
          <button onClick={() => setShowCsvModal(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-cyan-muted text-cyan border border-cyan/20 text-sm font-medium hover:bg-cyan/20 transition-colors cursor-pointer">
            <Icon name="upload" className="w-4 h-4" /> Import CSV
          </button>
        </PageHeader>

        {/* Toolbar: search + role filter */}
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <div className="relative flex-1 min-w-[220px]">
            <Icon name="search" className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-cream-muted" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search username…"
              className="w-full pl-9 pr-3 py-2 rounded-lg bg-ink-raised border border-border-default text-cream text-sm placeholder:text-cream-muted focus:outline-none focus:border-cyan/40 transition-colors" />
          </div>
          <div className="flex items-center gap-1 p-1 rounded-lg bg-ink-raised border border-border-subtle">
            {ROLE_FILTERS.map((r) => (
              <button key={r} onClick={() => setRoleFilter(r)}
                className={`px-3 py-1 rounded-md text-xs font-medium capitalize transition-colors cursor-pointer ${
                  roleFilter === r ? 'bg-cyan-muted text-cyan' : 'text-cream-secondary hover:text-cream'
                }`}>
                {r}
              </button>
            ))}
          </div>
        </div>

        {/* Table */}
        <div className="rounded-xl border border-border-subtle bg-ink-raised overflow-x-auto">
          <table className="w-full min-w-[560px] text-sm">
            <thead>
              <tr className="bg-ink-surface/40 border-b border-border-subtle text-[10px] uppercase tracking-widest text-cream-muted">
                <SortableTh label="User" active={sortKey === 'username'} dir={sortDir} onClick={() => toggleSort('username')} />
                <th className="text-left font-medium px-4 py-3">Role</th>
                <SortableTh label="Quota" active={sortKey === 'daily_token_quota'} dir={sortDir} onClick={() => toggleSort('daily_token_quota')} />
                <th className="text-right font-medium px-4 py-3">Actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((u) => (
                <tr key={u.id} className="border-b border-border-subtle last:border-0 hover:bg-ink-hover/40 transition-colors">
                  <td className="px-4 py-2.5 max-w-[240px]">
                    <p className="flex items-center gap-1.5 font-medium text-cream">
                      <span className="truncate">{u.username}</span>
                      {u.id === myId && <span className="shrink-0 rounded-full border border-cyan/40 px-1.5 text-[10px] font-normal text-cyan">you</span>}
                      {u.must_change_password && (
                        <span className="shrink-0 rounded-full border border-gold/50 px-1.5 text-[10px] font-normal text-gold"
                          title="The password was reset: the user chooses a new one at the next sign-in">must change password</span>
                      )}
                    </p>
                    {owned[u.id] && (
                      <p className="text-[11px] text-cream-muted truncate" title={owned[u.id].join(', ')}>
                        {plural(owned[u.id].length, 'class', 'classes')}: {owned[u.id].join(', ')}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-2.5">
                    <select value={u.role} onChange={(e) => handleRoleChange(u.id, e.target.value)}
                      disabled={u.role === 'admin'}
                      className="bg-ink-surface border border-border-default text-cream-secondary text-xs px-2 py-1 rounded-lg cursor-pointer focus:outline-none focus:border-cyan/40 disabled:opacity-50 disabled:cursor-not-allowed">
                      <option value="student">student</option>
                      <option value="teacher">teacher</option>
                      {u.role === 'admin' && <option value="admin">admin</option>}
                    </select>
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex flex-col gap-1">
                      {[['daily_token_quota', 'tok/day'], ['weekly_token_quota', 'tok/week']].map(([field, unit]) => (
                        <div key={field} className="flex items-center gap-1.5">
                          <input type="number" defaultValue={u[field]} min={0} step={100000} aria-label={`${u.username} ${unit}`}
                            onBlur={(e) => handleQuotaChange(u, field, e.target.value)}
                            className="w-28 bg-ink-surface border border-border-default text-cream-secondary text-xs px-2 py-1 rounded-lg focus:outline-none focus:border-cyan/40" />
                          <span className="text-[10px] text-cream-muted">{unit}</span>
                        </div>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center justify-end gap-1">
                      <IconButton title="Reset token limits (a full day and week again; usage is kept)" onClick={() => handleRefill(u)}>
                        <Icon name="reset" className="w-4 h-4" />
                      </IconButton>
                      <IconButton title={`Reset password to ${RESET_PASSWORD}`} onClick={() => setResetUser(u)}>
                        <Icon name="key" className="w-4 h-4" />
                      </IconButton>
                      <IconButton danger disabled={u.id === myId} onClick={() => setDeleteUser(u)}
                        title={u.id === myId ? 'You cannot delete your own account' : 'Delete user'}>
                        <Icon name="trash" className="w-4 h-4" />
                      </IconButton>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {visible.length === 0 && (
            <p className="text-center text-sm text-cream-muted py-10">No users match your filters.</p>
          )}
        </div>
      </div>

      {/* Reset password modal */}
      {resetUser && (
        <Modal title={`Reset password — ${resetUser.username}`} onClose={() => setResetUser(null)}>
          <div className="space-y-4">
            <p className="text-sm text-cream-secondary">
              The password of <span className="text-cream font-medium">{resetUser.username}</span> becomes{' '}
              <span className="rounded bg-ink-deep px-1.5 font-mono text-cream">{RESET_PASSWORD}</span>.
              At the next sign-in, {resetUser.username} must choose a new password before using the app.
            </p>
            <p className="text-xs text-cream-muted">Tell {resetUser.username} the password yourself: they are not notified.</p>
            <button onClick={handleResetPassword} disabled={resetting} autoFocus
              className="w-full py-3 rounded-lg gradient-cyan text-on-accent text-sm font-semibold hover:brightness-110 transition-all cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed">
              {resetting ? 'Resetting…' : `Reset to ${RESET_PASSWORD}`}
            </button>
          </div>
        </Modal>
      )}

      {/* Delete confirm modal */}
      {deleteUser && (
        <ConfirmModal
          title="Delete user"
          message={<>
            Delete user <span className="text-cream font-medium">{deleteUser.username}</span>? This is a soft delete and can be reviewed by an admin.
            {owned[deleteUser.id] && (
              <span className="block mt-3 text-danger">
                {deleteUser.username} still owns {plural(owned[deleteUser.id].length, 'class', 'classes')}: {owned[deleteUser.id].join(', ')}.
                Nobody will be able to manage {owned[deleteUser.id].length === 1 ? 'it' : 'them'}: transfer {owned[deleteUser.id].length === 1 ? 'it' : 'them'} first in Classes.
              </span>
            )}
          </>}
          confirmLabel="Delete"
          danger busy={deleting}
          onConfirm={handleDelete}
          onCancel={() => setDeleteUser(null)}
        />
      )}

      {/* CSV Modal */}
      {showCsvModal && (
        <Modal title="Import Users (CSV)" onClose={() => { setShowCsvModal(false); setCsvPreview(null); setCsvFile(null); }}>
          <p className="text-xs text-cream-muted mb-3">CSV columns: <code className="text-cyan">username, password, role</code> (role optional, defaults to student)</p>
          <input type="file" accept=".csv" onChange={(e) => { setCsvFile(e.target.files[0]); setCsvPreview(null); }}
            className="w-full text-sm text-cream-secondary mb-4 cursor-pointer" />
          {csvPreview && (
            <div className="mb-4 p-3 rounded-lg bg-ink-deep border border-border-subtle text-xs space-y-1">
              <p className="text-cream">Total rows: {csvPreview.total}</p>
              <p className="text-success">To create: {csvPreview.to_create}</p>
              <p className="text-gold">To update: {csvPreview.to_update}</p>
              {csvPreview.conflicts?.length > 0 && <p className="text-danger">Conflicts: {csvPreview.conflicts.length}</p>}
            </div>
          )}
          <div className="flex gap-3">
            {!csvPreview ? (
              <button onClick={handleCsvPreview} disabled={!csvFile || importing}
                className="flex-1 py-2 rounded-lg bg-cyan-muted text-cyan border border-cyan/20 text-sm font-medium cursor-pointer disabled:opacity-40">
                {importing ? 'Previewing…' : 'Preview'}
              </button>
            ) : (
              <button onClick={handleCsvConfirm} disabled={importing}
                className="flex-1 py-2 rounded-lg gradient-cyan text-on-accent text-sm font-semibold cursor-pointer hover:brightness-110 disabled:opacity-40">
                {importing ? 'Importing…' : `Confirm Import (${csvPreview.to_create + csvPreview.to_update})`}
              </button>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}
