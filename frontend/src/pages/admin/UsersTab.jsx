/**
 * admin/UsersTab.jsx — Admin "Users" tab: list, roles, create, reset, delete.
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import useAuthStore from '../../store/authStore';
import Icon from '../../components/Icon';
import { classesByTeacher } from '../../lib/analytics';
import { useT } from '../../lib/i18n';
import {
  PageHeader, SortableTh, IconButton, Spinner, Modal, ConfirmModal, useCount,
  inputClass, primaryButton, secondaryButton, outlineButton,
} from './shared';

/* ═══════════════════════════════ USERS TAB ═══════════════════════════════ */
const ROLE_FILTERS = ['all', 'student', 'teacher', 'admin'];
// The server's RESET_PASSWORD (backend config), shown to the admin.
const RESET_PASSWORD = '123456';

export default function UsersTab() {
  const t = useT();
  const count = useCount();
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
    catch { toast.error(t('a.users.loadFail')); } finally { setLoading(false); }
  }, [t]);

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
      toast.success(t('a.users.resetDone', { name: resetUser.username, password: RESET_PASSWORD }));
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
      toast.success(t('a.users.refilled', { name: user.username, day: (out.limit - out.used).toLocaleString(), week: (out.week_limit - out.week_used).toLocaleString() }));
    } catch (err) { toast.error(err.message); }
  };

  const handleDelete = async () => {
    if (!deleteUser) return;
    setDeleting(true);
    try {
      await api.delete(`/api/admin/users/${deleteUser.id}`);
      setUsers((p) => p.filter((x) => x.id !== deleteUser.id));
      toast.success(t('a.users.deleted'));
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
      toast.success(t('a.users.imported', { created: result.created, updated: result.updated }));
      setShowCsvModal(false); setCsvPreview(null); setCsvFile(null);
      load();
    } catch (err) { toast.error(err.message); } finally { setImporting(false); }
  };

  if (loading) return <Spinner />;

  const quotaUnits = [['daily_token_quota', 'a.users.perDay'], ['weekly_token_quota', 'a.users.perWeek']];

  return (
    <div className="p-4 md:p-8 overflow-y-auto h-full">
      <div className="max-w-5xl mx-auto">
        <PageHeader title={t('a.nav.users')} subtitle={t('a.users.total', { n: users.length })}>
          <button type="button" onClick={() => setShowCsvModal(true)} className={`${outlineButton} cursor-pointer`}>
            <Icon name="upload" className="w-4 h-4" /> {t('a.users.import')}
          </button>
        </PageHeader>

        {/* Toolbar: search + role filter */}
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <div className="relative flex-1 min-w-[220px]">
            <Icon name="search" className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-cream-muted" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t('a.users.search')}
              aria-label={t('a.users.search')} className={`w-full pl-9 ${inputClass}`} />
          </div>
          <div className="flex flex-wrap items-center gap-1 p-0.5 rounded-xl bg-ink-raised border border-border-subtle">
            {ROLE_FILTERS.map((r) => (
              <button key={r} type="button" onClick={() => setRoleFilter(r)}
                className={`px-3 py-1 rounded-lg text-[13px] transition-colors cursor-pointer ${
                  roleFilter === r ? 'bg-ink-hover text-cream' : 'text-cream-secondary hover:text-cream'
                }`}>
                {t(`a.role.${r}`)}
              </button>
            ))}
          </div>
        </div>

        {/* Table */}
        <div className="rounded-2xl border border-border-subtle bg-ink-raised overflow-x-auto">
          <table className="w-full min-w-[560px] text-[14px]">
            <thead>
              <tr className="border-b border-border-subtle text-[12.5px] text-cream-muted">
                <SortableTh label={t('a.users.col.user')} active={sortKey === 'username'} dir={sortDir} onClick={() => toggleSort('username')} />
                <th className="text-left font-normal px-4 py-2.5">{t('a.users.col.role')}</th>
                <SortableTh label={t('a.users.col.quota')} active={sortKey === 'daily_token_quota'} dir={sortDir} onClick={() => toggleSort('daily_token_quota')} />
                <th className="text-right font-normal px-4 py-2.5">{t('a.users.col.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((u) => (
                <tr key={u.id} className="border-b border-border-subtle last:border-0 hover:bg-ink-hover/40 transition-colors">
                  <td className="px-4 py-2.5 max-w-[240px]">
                    <p className="flex items-center gap-1.5 font-medium text-cream">
                      <span className="truncate">{u.username}</span>
                      {u.id === myId && <span className="shrink-0 rounded-full border border-border-default px-1.5 text-[11px] font-normal text-cream-secondary">{t('a.users.you')}</span>}
                      {u.must_change_password && (
                        <span className="shrink-0 rounded-full bg-warn-soft px-1.5 text-[11px] font-normal text-gold"
                          title={t('a.users.mustChangeHint')}>{t('a.users.mustChange')}</span>
                      )}
                    </p>
                    {owned[u.id] && (
                      <p className="text-[12.5px] text-cream-muted truncate" title={owned[u.id].join(', ')}>
                        {count('class', owned[u.id].length)}: {owned[u.id].join(', ')}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-2.5">
                    <select value={u.role} onChange={(e) => handleRoleChange(u.id, e.target.value)}
                      disabled={u.role === 'admin'} aria-label={`${u.username}: ${t('a.users.col.role')}`}
                      className="cursor-pointer rounded-lg border border-border-default bg-ink-raised px-2 py-1 text-[13px] text-cream focus:border-cream-muted focus:outline-none disabled:cursor-not-allowed disabled:opacity-50">
                      <option value="student">{t('a.role.student')}</option>
                      <option value="teacher">{t('a.role.teacher')}</option>
                      {u.role === 'admin' && <option value="admin">{t('a.role.admin')}</option>}
                    </select>
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex flex-col gap-1">
                      {quotaUnits.map(([field, unitKey]) => (
                        <div key={field} className="flex items-center gap-1.5">
                          <input type="number" defaultValue={u[field]} min={0} step={100000} aria-label={`${u.username} ${t(unitKey)}`}
                            onBlur={(e) => handleQuotaChange(u, field, e.target.value)}
                            className="w-28 rounded-lg border border-border-default bg-ink-raised px-2 py-1 text-[13px] text-cream focus:border-cream-muted focus:outline-none" />
                          <span className="text-[12.5px] text-cream-muted">{t(unitKey)}</span>
                        </div>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center justify-end gap-1">
                      <IconButton title={t('a.users.refill')} onClick={() => handleRefill(u)}>
                        <Icon name="reset" className="w-4 h-4" />
                      </IconButton>
                      <IconButton title={t('a.users.resetTo', { password: RESET_PASSWORD })} onClick={() => setResetUser(u)}>
                        <Icon name="key" className="w-4 h-4" />
                      </IconButton>
                      <IconButton danger disabled={u.id === myId} onClick={() => setDeleteUser(u)}
                        title={u.id === myId ? t('a.users.noSelfDelete') : t('a.users.deleteUser')}>
                        <Icon name="trash" className="w-4 h-4" />
                      </IconButton>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {visible.length === 0 && (
            <p className="text-center text-[14px] text-cream-muted py-10">{t('a.users.noMatch')}</p>
          )}
        </div>
      </div>

      {/* Reset password modal */}
      {resetUser && (
        <Modal title={t('a.users.resetTitle', { name: resetUser.username })} onClose={() => setResetUser(null)}>
          <div className="space-y-4">
            <p className="text-[14px] text-cream-secondary">
              {t('a.users.resetBody', { name: resetUser.username, password: RESET_PASSWORD })}
            </p>
            <p className="text-[13px] text-cream-muted">{t('a.users.resetNote', { name: resetUser.username })}</p>
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setResetUser(null)} disabled={resetting} className={`cursor-pointer ${secondaryButton}`}>{t('cancel')}</button>
              <button type="button" onClick={handleResetPassword} disabled={resetting} autoFocus className={`cursor-pointer ${primaryButton}`}>
                {resetting ? t('a.users.resetting') : t('a.users.resetButton', { password: RESET_PASSWORD })}
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* Delete confirm modal */}
      {deleteUser && (
        <ConfirmModal
          title={t('a.users.deleteUser')}
          message={<>
            {t('a.users.deleteBody', { name: deleteUser.username })}
            {owned[deleteUser.id] && (
              <span className="block mt-3 text-danger">
                {t(owned[deleteUser.id].length === 1 ? 'a.users.ownsOne' : 'a.users.ownsMany',
                  { name: deleteUser.username, classes: owned[deleteUser.id].join(', '), n: owned[deleteUser.id].length })}
              </span>
            )}
          </>}
          confirmLabel={t('delete')}
          danger busy={deleting}
          onConfirm={handleDelete}
          onCancel={() => setDeleteUser(null)}
        />
      )}

      {/* CSV Modal */}
      {showCsvModal && (
        <Modal title={t('a.users.csvTitle')} onClose={() => { setShowCsvModal(false); setCsvPreview(null); setCsvFile(null); }}>
          <p className="text-[13px] text-cream-muted mb-3">
            {t('a.users.csvColumns')} <code className="text-cream">username, password, role</code> {t('a.users.csvRole')}
          </p>
          <input type="file" accept=".csv" onChange={(e) => { setCsvFile(e.target.files[0]); setCsvPreview(null); }}
            aria-label={t('a.users.csvFile')} className="w-full text-[14px] text-cream-secondary mb-4 cursor-pointer" />
          {csvPreview && (
            <div className="mb-4 p-3 rounded-xl border border-border-subtle text-[13px] space-y-1">
              <p className="text-cream">{t('a.users.csvTotal', { n: csvPreview.total })}</p>
              <p className="text-success">{t('a.users.csvCreate', { n: csvPreview.to_create })}</p>
              <p className="text-gold">{t('a.users.csvUpdate', { n: csvPreview.to_update })}</p>
              {csvPreview.conflicts?.length > 0 && <p className="text-danger">{t('a.users.csvConflicts', { n: csvPreview.conflicts.length })}</p>}
            </div>
          )}
          <div className="flex justify-end gap-2">
            {!csvPreview ? (
              <button type="button" onClick={handleCsvPreview} disabled={!csvFile || importing} className={`cursor-pointer ${primaryButton}`}>
                {importing ? t('a.users.previewing') : t('a.users.preview')}
              </button>
            ) : (
              <button type="button" onClick={handleCsvConfirm} disabled={importing} className={`cursor-pointer ${primaryButton}`}>
                {importing ? t('a.users.importing') : t('a.users.confirmImport', { n: csvPreview.to_create + csvPreview.to_update })}
              </button>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}
