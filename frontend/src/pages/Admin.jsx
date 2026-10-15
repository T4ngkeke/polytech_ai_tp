/**
 * Admin.jsx — God-mode dashboard v7.2.
 * Left nav rail: Users | Oversight | Analytics | LLM Config | Maintenance.
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import toast from 'react-hot-toast';
import { useNavigate } from 'react-router-dom';
import api from '../lib/api';
import useAuthStore from '../store/authStore';
import ThemeToggle from '../components/ThemeToggle';
import Icon from '../components/Icon';

const NAV = [
  { key: 'users', label: 'Users', icon: UsersIcon },
  { key: 'oversight', label: 'Oversight', icon: GridIcon },
  { key: 'analytics', label: 'Analytics', icon: ChartIcon },
  { key: 'llm', label: 'LLM Config', icon: ChipIcon },
  { key: 'maintenance', label: 'Maintenance', icon: DatabaseIcon },
];

export default function Admin() {
  const [active, setActive] = useState('users');
  // Phones: the navigation is a drawer opened from the top bar.
  const [navOpen, setNavOpen] = useState(false);
  const { username, logout } = useAuthStore();
  const navigate = useNavigate();

  const handleSignOut = () => {
    logout();
    navigate('/login', { replace: true });
  };

  return (
    <div className="flex flex-col md:flex-row h-dvh bg-ink-deep overflow-hidden">
      <div className="md:hidden shrink-0 h-12 px-2 flex items-center gap-2 border-b border-border-subtle bg-ink-base">
        <button type="button" onClick={() => setNavOpen(true)} title="Menu" aria-label="Menu"
          className="p-2 rounded-lg text-cream-secondary hover:text-cream hover:bg-ink-hover cursor-pointer">
          <MenuIcon className="w-5 h-5" />
        </button>
        <span className="text-sm font-medium text-cream">{NAV.find((n) => n.key === active)?.label}</span>
      </div>
      {navOpen && (
        <div className="fixed inset-0 z-30 bg-black/40 md:hidden" onClick={() => setNavOpen(false)} />
      )}
      {/* Nav rail (a drawer on phones) */}
      <aside className={`${navOpen ? 'flex' : 'hidden'} fixed inset-y-0 left-0 z-40 w-64 shadow-elevated bg-ink-base
        md:static md:z-auto md:flex md:w-56 md:shadow-none md:bg-ink-base/50 shrink-0 flex-col border-r border-border-subtle`}>
        <div className="px-5 pt-6 pb-5">
          <h1 className="font-display text-lg text-cream leading-tight">Admin Console</h1>
          <p className="text-[11px] text-cream-muted mt-0.5">System administration</p>
        </div>
        <nav className="flex-1 px-3 space-y-1">
          {NAV.map(({ key, label, icon: Icon }) => {
            const on = active === key;
            return (
              <button key={key} onClick={() => { setActive(key); setNavOpen(false); }}
                className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors cursor-pointer ${
                  on ? 'bg-cyan-muted text-cyan' : 'text-cream-secondary hover:text-cream hover:bg-ink-hover'
                }`}>
                <Icon className="w-[18px] h-[18px] shrink-0" />
                {label}
              </button>
            );
          })}
        </nav>
        <div className="p-3 border-t border-border-subtle">
          <div className="flex items-center gap-2 px-2 py-1.5">
            <div className="w-7 h-7 rounded-full bg-cyan-muted text-cyan grid place-items-center text-xs font-semibold shrink-0">
              {(username || 'A').charAt(0).toUpperCase()}
            </div>
            <span className="flex-1 min-w-0 text-sm font-medium text-cream truncate">{username || 'Admin'}</span>
            <ThemeToggle className="!p-1.5 !bg-transparent !border-transparent" />
            <button onClick={handleSignOut} title="Sign out"
              className="p-1.5 rounded-lg text-cream-muted hover:text-danger hover:bg-danger-muted transition-colors cursor-pointer">
              <LogoutIcon className="w-4 h-4" />
            </button>
          </div>
        </div>
      </aside>

      {/* Content */}
      <main className="flex-1 min-h-0 overflow-hidden">
        {active === 'users' && <UsersTab />}
        {active === 'oversight' && <OversightTab />}
        {active === 'analytics' && <AnalyticsTab />}
        {active === 'llm' && <LLMConfigTab />}
        {active === 'maintenance' && <MaintenanceTab />}
      </main>
    </div>
  );
}

/* ═══════════════════════════════ USERS TAB ═══════════════════════════════ */
const ROLE_FILTERS = ['all', 'student', 'teacher', 'admin'];

function UsersTab() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCsvModal, setShowCsvModal] = useState(false);
  const [csvFile, setCsvFile] = useState(null);
  const [csvPreview, setCsvPreview] = useState(null);
  const [importing, setImporting] = useState(false);
  const [resetUser, setResetUser] = useState(null);   // user being password-reset
  const [resetPw, setResetPw] = useState('');
  const [resetting, setResetting] = useState(false);
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

  const handleResetPassword = async () => {
    if (!resetUser || resetPw.length < 6) return;
    setResetting(true);
    try {
      await api.put(`/api/admin/users/${resetUser.id}/password`, { new_password: resetPw });
      toast.success(`Password reset for ${resetUser.username}`);
      setResetUser(null);
      setResetPw('');
    } catch (err) { toast.error(err.message); } finally { setResetting(false); }
  };

  const handleQuotaChange = async (userId, quota) => {
    try {
      const u = await api.put(`/api/admin/users/${userId}/quota`, { daily_token_quota: Number(quota) });
      setUsers((p) => p.map((x) => x.id === userId ? { ...x, daily_token_quota: u.daily_token_quota } : x));
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
            <UploadIcon className="w-4 h-4" /> Import CSV
          </button>
        </PageHeader>

        {/* Toolbar: search + role filter */}
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <div className="relative flex-1 min-w-[220px]">
            <SearchIcon className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-cream-muted" />
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
                  <td className="px-4 py-2.5 font-medium text-cream truncate max-w-[240px]">{u.username}</td>
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
                    <div className="flex items-center gap-1.5">
                      <input type="number" defaultValue={u.daily_token_quota} min={0} step={1000}
                        onBlur={(e) => handleQuotaChange(u.id, e.target.value)}
                        className="w-24 bg-ink-surface border border-border-default text-cream-secondary text-xs px-2 py-1 rounded-lg focus:outline-none focus:border-cyan/40" />
                      <span className="text-[10px] text-cream-muted">tok/day</span>
                    </div>
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center justify-end gap-1">
                      <IconButton title="Reset password" onClick={() => { setResetUser(u); setResetPw(''); }}>
                        <KeyIcon className="w-4 h-4" />
                      </IconButton>
                      <IconButton title="Delete user" danger onClick={() => setDeleteUser(u)}>
                        <TrashIcon className="w-4 h-4" />
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
            <p className="text-xs text-cream-muted">Set a new password for this user. They are not notified — share it securely.</p>
            <input type="text" value={resetPw} autoFocus
              onChange={(e) => setResetPw(e.target.value)} placeholder="New password (min 6 chars)"
              className="w-full px-4 py-3 rounded-lg bg-ink-deep border border-border-default text-cream text-sm font-mono placeholder:text-cream-muted focus:outline-none focus:border-cyan/40" />
            <button onClick={handleResetPassword} disabled={resetPw.length < 6 || resetting}
              className="w-full py-3 rounded-lg gradient-cyan text-on-accent text-sm font-semibold hover:brightness-110 transition-all cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed">
              {resetting ? 'Resetting…' : 'Reset password'}
            </button>
          </div>
        </Modal>
      )}

      {/* Delete confirm modal */}
      {deleteUser && (
        <ConfirmModal
          title="Delete user"
          message={<>Delete user <span className="text-cream font-medium">{deleteUser.username}</span>? This is a soft delete and can be reviewed by an admin.</>}
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

/* ═══════════════════════════ OVERSIGHT TAB ═══════════════════════════════ */
// Breadcrumb drill-down: Classes list → class detail (Labs + Students).
// Single content panel, no nested sidebar.
function OversightTab() {
  const [classes, setClasses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [teachers, setTeachers] = useState([]);
  const [selectedClassId, setSelectedClassId] = useState(null);
  const [students, setStudents] = useState([]);
  const [studentsLoading, setStudentsLoading] = useState(false);
  // modal targets
  const [deleteClass, setDeleteClass] = useState(null);
  const [deleteLab, setDeleteLab] = useState(null);
  const [transferClass, setTransferClass] = useState(null);
  const [transferTo, setTransferTo] = useState('');
  const [busy, setBusy] = useState(false);

  // derive the open class from `classes` so lab edits reflect live
  const selectedClass = classes.find((c) => c.id === selectedClassId) || null;

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const cls = await api.get('/api/admin/classes');
      const withLabs = await Promise.all(cls.map(async (c) => {
        const l = await api.get(`/api/admin/classes/${c.id}/labs`).catch(() => []);
        return { ...c, labs: l };
      }));
      setClasses(withLabs);
    } catch { toast.error('Failed to load classes'); } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    // teacher list for the transfer dropdown
    api.get('/api/admin/users')
      .then((us) => setTeachers(us.filter((u) => u.role === 'teacher' || u.role === 'admin')))
      .catch(() => {});
  }, []);

  const openClass = (cls) => {
    setSelectedClassId(cls.id);
    setStudents([]); setStudentsLoading(true);
    api.get(`/api/admin/classes/${cls.id}/students`)
      .then(setStudents).catch(() => {}).finally(() => setStudentsLoading(false));
  };

  const doDeleteClass = async () => {
    setBusy(true);
    try {
      await api.delete(`/api/admin/classes/${deleteClass.id}`);
      setClasses((p) => p.filter((c) => c.id !== deleteClass.id));
      if (selectedClassId === deleteClass.id) setSelectedClassId(null);
      toast.success('Class deleted');
      setDeleteClass(null);
    } catch (err) { toast.error(err.message); } finally { setBusy(false); }
  };

  const doTransferClass = async () => {
    if (!transferTo) return;
    setBusy(true);
    try {
      const updated = await api.put(`/api/admin/classes/${transferClass.id}/transfer`, { teacher_id: transferTo });
      setClasses((p) => p.map((c) => c.id === transferClass.id ? { ...c, teacher_id: updated.teacher_id } : c));
      toast.success('Class transferred');
      setTransferClass(null);
    } catch (err) { toast.error(err.message); } finally { setBusy(false); }
  };

  const doDeleteLab = async () => {
    setBusy(true);
    try {
      await api.delete(`/api/admin/labs/${deleteLab.id}`);
      setClasses((p) => p.map((c) => ({ ...c, labs: c.labs?.filter((l) => l.id !== deleteLab.id) })));
      toast.success('Lab deleted');
      setDeleteLab(null);
    } catch (err) { toast.error(err.message); } finally { setBusy(false); }
  };

  const doToggleLab = async (lab) => {
    try {
      const updated = await api.put(`/api/admin/labs/${lab.id}`, { is_active: !lab.is_active });
      setClasses((p) => p.map((c) => ({ ...c, labs: c.labs?.map((l) => l.id === lab.id ? updated : l) })));
      toast.success(updated.is_active ? 'Lab unlocked' : 'Lab locked');
    } catch (err) { toast.error(err.message); }
  };

  return (
    <div className="p-4 md:p-8 overflow-y-auto h-full">
      <div className="max-w-3xl mx-auto">
        {!selectedClass ? (
          /* ── Level 0: class list ── */
          <>
            <PageHeader title="Oversight" subtitle={`${plural(classes.length, 'class', 'classes')} across the platform`} />
            {loading ? <Spinner /> : classes.length === 0 ? (
              <EmptyState icon={<GridIcon className="w-7 h-7" />} message="No classes on the platform yet." />
            ) : (
              <div className="rounded-xl border border-border-subtle bg-ink-raised overflow-hidden divide-y divide-border-subtle">
                {classes.map((cls) => (
                  <div key={cls.id} onClick={() => openClass(cls)}
                    className="group flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-ink-hover/50 transition-colors">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-cream truncate">{cls.name}</p>
                      <p className="text-[11px] text-cream-muted mt-0.5">{plural(cls.labs?.length || 0, 'lab')}</p>
                    </div>
                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity"
                      onClick={(e) => e.stopPropagation()}>
                      <IconButton title="Transfer ownership" onClick={() => { setTransferClass(cls); setTransferTo(''); }}>
                        <TransferIcon className="w-4 h-4" />
                      </IconButton>
                      <IconButton title="Force-delete class" danger onClick={() => setDeleteClass(cls)}>
                        <TrashIcon className="w-4 h-4" />
                      </IconButton>
                    </div>
                    <ChevronIcon className="w-4 h-4 text-cream-muted shrink-0" />
                  </div>
                ))}
              </div>
            )}
          </>
        ) : (
          /* ── Level 1: class detail (Labs + Students) ── */
          <>
            <button onClick={() => setSelectedClassId(null)}
              className="inline-flex items-center gap-1 text-xs text-cream-muted hover:text-cyan transition-colors cursor-pointer mb-4">
              <ChevronIcon className="w-3.5 h-3.5 rotate-180" /> Classes
            </button>
            <PageHeader title={selectedClass.name}
              subtitle={`${plural(selectedClass.labs?.length || 0, 'lab')} · ${plural(students.length, 'student')}`} />

            {/* Labs */}
            <SectionLabel>Labs ({selectedClass.labs?.length || 0})</SectionLabel>
            {selectedClass.labs?.length ? (
              <div className="rounded-xl border border-border-subtle bg-ink-raised overflow-hidden divide-y divide-border-subtle mb-8">
                {selectedClass.labs.map((lab) => {
                  const locked = lab.is_deleted || !lab.is_active;
                  return (
                    <div key={lab.id} className="group flex items-center gap-3 px-4 py-2.5">
                      <span className={`shrink-0 w-1.5 h-1.5 rounded-full ${locked ? 'bg-cream-muted' : 'bg-success'}`} />
                      <span className="flex-1 min-w-0 text-sm text-cream truncate">{lab.name}</span>
                      <span className="text-[10px] uppercase tracking-wider text-cream-muted">{locked ? 'Locked' : 'Active'}</span>
                      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                        <IconButton title={lab.is_active ? 'Lock lab' : 'Unlock lab'} onClick={() => doToggleLab(lab)}>
                          {lab.is_active ? <LockIcon className="w-4 h-4" /> : <UnlockIcon className="w-4 h-4" />}
                        </IconButton>
                        <IconButton title="Force-delete lab" danger onClick={() => setDeleteLab(lab)}>
                          <TrashIcon className="w-4 h-4" />
                        </IconButton>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="text-cream-muted text-sm mb-8">No labs in this class.</p>
            )}

            {/* Students */}
            <SectionLabel>Students ({students.length})</SectionLabel>
            {studentsLoading ? (
              <p className="text-cream-muted text-sm">Loading…</p>
            ) : students.length ? (
              <div className="rounded-xl border border-border-subtle bg-ink-raised overflow-hidden divide-y divide-border-subtle">
                {students.map((s) => (
                  <div key={s.id} className="flex items-center justify-between px-4 py-2.5 text-sm hover:bg-ink-hover/40 transition-colors">
                    <span className="text-cream">{s.username}</span>
                    <span className="text-cream-muted font-mono text-xs capitalize">{s.role}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-cream-muted text-sm">No students in this class yet.</p>
            )}
          </>
        )}
      </div>

      {deleteClass && (
        <ConfirmModal title="Force-delete class" danger busy={busy}
          message={<>Force-delete class <span className="text-cream font-medium">{deleteClass.name}</span>? This cannot be undone.</>}
          confirmLabel="Delete class" onConfirm={doDeleteClass} onCancel={() => setDeleteClass(null)} />
      )}
      {deleteLab && (
        <ConfirmModal title="Force-delete lab" danger busy={busy}
          message={<>Force-delete lab <span className="text-cream font-medium">{deleteLab.name}</span>?</>}
          confirmLabel="Delete lab" onConfirm={doDeleteLab} onCancel={() => setDeleteLab(null)} />
      )}
      {transferClass && (
        <Modal title={`Transfer "${transferClass.name}"`} onClose={() => setTransferClass(null)}>
          <div className="space-y-4">
            <p className="text-xs text-cream-muted">Choose the teacher who will own this class.</p>
            <select value={transferTo} onChange={(e) => setTransferTo(e.target.value)}
              className="w-full px-4 py-3 rounded-lg bg-ink-deep border border-border-default text-cream text-sm cursor-pointer focus:outline-none focus:border-cyan/40">
              <option value="">Select a teacher…</option>
              {teachers.map((t) => <option key={t.id} value={t.id}>{t.username} ({t.role})</option>)}
            </select>
            <button onClick={doTransferClass} disabled={!transferTo || busy}
              className="w-full py-3 rounded-lg gradient-cyan text-on-accent text-sm font-semibold hover:brightness-110 transition-all cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed">
              {busy ? 'Transferring…' : 'Transfer ownership'}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

/* ═══════════════════════════ ANALYTICS TAB ═══════════════════════════════ */
function AnalyticsTab() {
  const [global, setGlobal] = useState(null);
  const [classes, setClasses] = useState([]);
  const [classAnalytics, setClassAnalytics] = useState({});
  const [expanded, setExpanded] = useState({});

  useEffect(() => {
    api.get('/api/admin/analytics').then(setGlobal).catch(() => {});
    api.get('/api/admin/classes').then(setClasses).catch(() => {});
  }, []);

  const loadClassAnalytics = async (classId) => {
    if (classAnalytics[classId]) {
      setExpanded((p) => ({ ...p, [classId]: !p[classId] }));
      return;
    }
    try {
      const data = await api.get(`/api/admin/analytics/classes/${classId}`);
      setClassAnalytics((p) => ({ ...p, [classId]: data }));
      setExpanded((p) => ({ ...p, [classId]: true }));
    } catch (err) { toast.error(err.message); }
  };

  return (
    <div className="p-4 md:p-8 overflow-y-auto h-full">
      <div className="max-w-4xl mx-auto space-y-6">
        <PageHeader title="Analytics" subtitle="Platform-wide token usage" />
        {global && (
          <div className="grid grid-cols-3 gap-4">
            <StatCard label="Platform Tokens" value={global.total_tokens?.toLocaleString()} />
            <StatCard label="Platform Requests" value={global.total_requests?.toLocaleString()} />
            <StatCard label="Classes" value={classes.length} />
          </div>
        )}
        <div>
          <h3 className="text-[10px] font-semibold uppercase tracking-widest text-cream-muted mb-3">Class Breakdown</h3>
          <div className="space-y-2">
            {classes.map((cls) => {
              const data = classAnalytics[cls.id];
              const open = expanded[cls.id];
              return (
                <div key={cls.id} className="bg-ink-raised rounded-xl border border-border-subtle overflow-hidden">
                  <button onClick={() => loadClassAnalytics(cls.id)}
                    className="w-full flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-ink-hover transition-colors">
                    <div className="flex items-center gap-2">
                      <ChevronIcon className={`w-4 h-4 text-cream-muted transition-transform ${open ? 'rotate-90' : ''}`} />
                      <span className="text-sm font-medium text-cream">{cls.name}</span>
                    </div>
                    {data && <span className="text-xs font-mono text-cyan">{data.total_tokens?.toLocaleString()} tok</span>}
                  </button>
                  {open && data && (
                    <div className="px-4 pb-4 border-t border-border-subtle pt-3 space-y-3">
                      {data.labs?.map((lab) => (
                        <div key={lab.lab_id} className="pl-3 border-l border-border-subtle">
                          <div className="flex justify-between text-xs mb-1">
                            <span className="text-cream-secondary font-medium">{lab.lab_name}</span>
                            <span className="text-cream-muted font-mono">{lab.tokens?.toLocaleString()} tok</span>
                          </div>
                          {lab.students?.map((s) => (
                            <div key={s.user_id} className="flex justify-between text-[11px] pl-3 py-0.5">
                              <span className="text-cream-muted">{s.username}</span>
                              <span className="text-cream-muted font-mono">{s.tokens_used?.toLocaleString()} tok · {s.request_count} req</span>
                            </div>
                          ))}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════ LLM CONFIG TAB — system map [v8.1] ══════════════════ */
// The model-routing table drawn as the system it configures: grey source
// nodes (students / worker) call the model slots; an empty slot either
// inherits the main LLM (dashed gold return edge) or is simply off. Click a
// node to edit just that slot — each panel saves independently (the backend
// PUT is a partial upsert).

// Who calls the models. Each node lists its callers in `sources`: a source
// or another node (the research agent calls embedding, reranker and vision).
const MAP_SOURCES = [
  { id: 'students', x: 10, y: 14, label: 'Students', sub: 'questions in a lab' },
  { id: 'teachers', x: 10, y: 48, label: 'Teachers', sub: 'figure descriptions' },
  { id: 'worker', x: 10, y: 82, label: 'Worker', sub: 'document uploads' },
];

// status(config): 'live' (the default model itself) · 'set' (own model/endpoint) ·
// 'inherits' (empty: uses the default model) · 'off' (feature disabled) ·
// 'deployment' (empty: uses the deployment setting) · 'legacy' (unused while
// the research agent is on).
const MAP_NODES = [
  {
    id: 'agent', x: 34, y: 14, icon: 'bot', name: 'Research agent', sources: ['students'],
    tag: 'Answers every student question in a lab: it searches and reads the course PDFs, '
      + 'may ask the student back, then answers with citations. The limits below apply to each question.',
    status: (c) => (c.agent_model ? 'set' : 'inherits'),
    fields: [
      { key: 'agent_model', label: 'Model Name', placeholder: 'inherits the default model' },
      { key: 'agent_base_url', label: 'Base URL', placeholder: 'inherits the default model' },
      { key: 'agent_api_key', label: 'API Key', placeholder: 'inherits the default model', secret: true },
      // Research limits: default and range come from the backend (research_limits).
      { key: 'agent_max_tool_calls', label: 'Course tool calls', limit: true },
      { key: 'agent_max_visual_calls', label: 'Image views', limit: true },
      { key: 'agent_research_seconds', label: 'Research time (s)', limit: true },
      { key: 'agent_total_seconds', label: 'Total time, answer included (s)', limit: true },
      { key: 'agent_call_seconds', label: 'One model call (s)', limit: true },
      { key: 'agent_max_input_tokens', label: 'Input tokens per model call', limit: true },
    ],
  },
  {
    id: 'vlm', x: 34, y: 48, icon: 'eye', name: 'Vision (VLM)', sources: ['agent', 'teachers', 'worker'],
    tag: 'Looks at images: page images and figures the research agent views, figure descriptions '
      + 'teachers ask for, and the worker’s repair of garbled formulas. Empty model = off; it never '
      + 'falls back to a text-only model.',
    status: (c) => (c.vlm_model ? 'set' : 'off'),
    fields: [
      { key: 'vlm_model', label: 'Model Name', placeholder: 'empty = off' },
      { key: 'vlm_base_url', label: 'Base URL', placeholder: 'inherits the default model URL' },
      { key: 'vlm_api_key', label: 'API Key', placeholder: 'inherits the default model key', secret: true },
    ],
  },
  {
    id: 'chat', x: 85, y: 48, icon: 'message', name: 'Default model', sources: [],
    tag: 'Every empty model slot uses this model (dashed lines). It also answers chats outside a lab '
      + '(the old chat path) and holds the billing weights α/β applied to every model call.',
    status: () => 'live',
    fields: [
      { key: 'model', label: 'Model Name', placeholder: 'served model name' },
      { key: 'base_url', label: 'Base URL', placeholder: 'http://model-server:8000/v1' },
      { key: 'api_key', label: 'API Key', placeholder: "sk-… or 'local'", secret: true },
      { key: 'context_max_tokens', label: 'Max history tokens sent to the model', placeholder: '8000' },
      { key: 'token_alpha', label: 'α — prefill weight', placeholder: '0.2', numeric: true },
      { key: 'token_beta', label: 'β — decode weight', placeholder: '1.0', numeric: true },
    ],
  },
  {
    id: 'embedding', x: 58, y: 48, icon: 'network', name: 'Embedding', sources: ['agent', 'worker'],
    tag: 'bge-m3 vectors. The worker indexes documents with it and the research search queries with it: '
      + 'both must use the same model.',
    status: (c) => (c.embedding_url ? 'set' : 'inherits'),
    fields: [
      { key: 'embedding_model', label: 'Model Name', placeholder: 'bge-m3' },
      { key: 'embedding_url', label: 'Base URL', placeholder: 'inherits the default model' },
      { key: 'embedding_api_key', label: 'API Key', placeholder: 'inherits the default model', secret: true },
    ],
  },
  {
    id: 'rerank', x: 58, y: 14, icon: 'sort', name: 'Reranker', sources: ['agent'],
    tag: 'Reorders the research search results (bge-reranker behind a /rerank endpoint, not the chat '
      + 'base URL). Empty URL = off: fusion order only. The score threshold applies only to the old chat path.',
    status: (c) => (c.rerank_url ? 'set' : 'off'),
    fields: [
      { key: 'rerank_model', label: 'Model Name', placeholder: 'BAAI/bge-reranker-v2-m3' },
      { key: 'rerank_url', label: 'Rerank URL', placeholder: 'http://reranker:8000/rerank (empty = off)' },
      { key: 'rerank_api_key', label: 'API Key', placeholder: '', secret: true },
      { key: 'rerank_score_threshold', label: 'Score threshold, old chat path only (empty = off)', placeholder: 'e.g. 0.35 once calibrated' },
    ],
  },
  {
    id: 'aux', x: 85, y: 82, icon: 'compass', name: 'Old chat path', sources: [],
    tag: 'Router, self-evaluation and rewrite of the chat path without the research agent (chats outside '
      + 'a lab, or the agent turned off). Not used while the research agent is on.',
    status: (c) => (c.features?.research_agent ? 'legacy' : c.router_model ? 'set' : 'inherits'),
    fields: [
      { key: 'router_model', label: 'Model Name', placeholder: 'inherits the default model' },
      { key: 'router_base_url', label: 'Base URL', placeholder: 'inherits the default model' },
      { key: 'router_api_key', label: 'API Key', placeholder: 'inherits the default model', secret: true },
    ],
  },
  {
    id: 'ingest', x: 34, y: 82, icon: 'download', name: 'Ingest model', sources: ['worker'],
    tag: 'Writes one line of context for each chunk when a document is indexed (worker, off-peak). '
      + 'The token budget per document is recorded in the ingest report.',
    status: (c) => (c.ingest_model ? 'set' : 'inherits'),
    fields: [
      { key: 'ingest_model', label: 'Model Name', placeholder: 'inherits the default model' },
      { key: 'ingest_base_url', label: 'Base URL', placeholder: 'inherits the default model' },
      { key: 'ingest_api_key', label: 'API Key', placeholder: 'inherits the default model', secret: true },
      { key: 'ingest_token_budget', label: 'Token budget / document', placeholder: '200000' },
    ],
  },
  {
    id: 'mineru', x: 58, y: 82, icon: 'file', name: 'MinerU (PDF)', sources: ['worker'],
    tag: 'Reads each page of an uploaded PDF (layout, text, tables, formulas) through a MinerU vision '
      + 'server. Only the server can change here, without a restart; the MinerU client, its local models '
      + 'and mineru.json are part of the worker deployment.',
    status: (c) => (c.mineru_server_url ? 'set' : c.features?.pdf_documents ? 'deployment' : 'off'),
    shows: (c) => c.mineru_server_url || c.mineru_default_url,
    fields: [
      { key: 'mineru_server_url', label: 'MinerU server URL', placeholder: 'empty = the deployment setting',
        hint: (c) => (c.mineru_default_url ? `Deployment setting: ${c.mineru_default_url}` : 'No deployment setting.') },
    ],
  },
];

const NODE_STATUS_META = {
  live: { chip: 'default', cls: 'bg-cyan-muted text-cyan' },
  set: { chip: 'own setting', cls: 'bg-success-muted text-success' },
  inherits: { chip: 'inherits', cls: 'bg-gold-muted text-gold' },
  deployment: { chip: 'deployment', cls: 'bg-success/10 text-success' },
  legacy: { chip: 'not used', cls: 'bg-ink-surface text-cream-muted' },
  off: { chip: 'off', cls: 'bg-ink-surface text-cream-muted' },
};

function MapEdges({ config }) {
  const byId = Object.fromEntries([...MAP_SOURCES, ...MAP_NODES].map((s) => [s.id, s]));
  const fallback = byId.chat;
  return (
    <svg className="absolute inset-0 h-full w-full" viewBox="0 0 100 100"
      preserveAspectRatio="none" aria-hidden="true">
      {MAP_NODES.flatMap((n) => n.sources.map((sourceId) => {
        const s = byId[sourceId];
        const st = n.status(config);
        return (
          <line key={`call-${sourceId}-${n.id}`} x1={s.x} y1={s.y} x2={n.x} y2={n.y}
            className={st === 'off' || st === 'legacy' ? 'stroke-cream-muted/30' : 'stroke-cyan/40'}
            strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
        );
      }))}
      {MAP_NODES.filter((n) => n.status(config) === 'inherits').map((n) => (
        <line key={`fb-${n.id}`} x1={n.x} y1={n.y} x2={fallback.x} y2={fallback.y}
          className="stroke-gold/60"
          strokeWidth="1.5" strokeDasharray="5 4" vectorEffect="non-scaling-stroke" />
      ))}
    </svg>
  );
}

function MapNode({ node, config, selected, onSelect }) {
  const st = node.status(config);
  const meta = NODE_STATUS_META[st];
  const model = node.shows ? node.shows(config) : config[node.fields[0].key];
  return (
    <button type="button" onClick={onSelect}
      style={{ left: `${node.x}%`, top: `${node.y}%` }}
      className={`absolute w-36 -translate-x-1/2 -translate-y-1/2 rounded-xl border p-2 text-left transition-all cursor-pointer bg-ink-raised hover:border-cyan/50 ${selected
        ? 'border-cyan ring-2 ring-cyan/30 shadow-glow'
        : 'border-border-default'}`}>
      <div className="flex items-center justify-between gap-1">
        <span className="flex min-w-0 items-center gap-1.5">
          <Icon name={node.icon} className="w-4 h-4 shrink-0 text-cyan" />
          <span className="truncate text-xs font-semibold text-cream">{node.name}</span>
        </span>
        {node.warnEmpty && st === 'inherits' ? (
          <span className="text-danger" title={node.warnEmpty}><Icon name="warning" className="w-3.5 h-3.5" /></span>
        ) : null}
      </div>
      <p className="mt-1 truncate font-mono text-[10px] text-cream-muted">
        {model || (st === 'off' || st === 'legacy' ? '—' : 'default model')}
      </p>
      <span className={`mt-1 inline-block rounded px-1 py-px text-[9px] font-medium ${meta.cls}`}>
        {meta.chip}
      </span>
    </button>
  );
}

function MapSource({ source }) {
  return (
    <div style={{ left: `${source.x}%`, top: `${source.y}%` }}
      className="absolute w-28 -translate-x-1/2 -translate-y-1/2 rounded-lg border border-dashed border-border-subtle bg-ink-deep/60 p-2 text-center">
      <p className="text-xs font-semibold text-cream-secondary">{source.label}</p>
      <p className="text-[10px] text-cream-muted">{source.sub}</p>
    </div>
  );
}

function NodePanel({ node, config, set, onSave, saving }) {
  const st = node.status(config);
  return (
    <div className="space-y-3 rounded-xl border border-border-default bg-ink-deep/30 p-4">
      <div>
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-cream">
          <Icon name={node.icon} className="w-4 h-4 text-cyan" />{node.name}
        </h3>
        <p className="mt-1 text-xs leading-relaxed text-cream-muted">{node.tag}</p>
      </div>
      {node.fields.map((f, i) => {
        if (f.secret) {
          return <SecretField key={f.key} label={f.label} value={config[f.key]}
            onChange={set(f.key)} placeholder={f.placeholder} />;
        }
        const limit = f.limit ? config.research_limits?.[f.key] : null;
        const field = <Field key={f.key} value={config[f.key]} onChange={set(f.key)}
          label={limit ? `${f.label} · ${limit.min}–${limit.max}` : f.label}
          placeholder={limit ? `default ${limit.default}` : f.placeholder} />;
        if (f.hint) {
          return (
            <div key={f.key} className="space-y-1">
              {field}
              <p className="text-[11px] text-cream-muted">{f.hint(config)}</p>
            </div>
          );
        }
        // A heading before the first research limit.
        return f.limit && !node.fields[i - 1]?.limit ? (
          <div key={f.key} className="space-y-3">
            <div className="border-t border-border-subtle pt-3">
              <p className="text-xs font-semibold text-cream">Research limits, per student question</p>
              <p className="mt-1 text-xs leading-relaxed text-cream-muted">
                Empty uses the default. The time between research and total is left for the
                answer: keep it at least one model call long.
              </p>
            </div>
            {field}
          </div>
        ) : field;
      })}
      {node.warnEmpty && st === 'inherits' ? (
        <p className="text-xs text-gold"><Icon name="warning" className="inline-block w-3.5 h-3.5 align-[-2px]" /> {node.warnEmpty}</p>
      ) : null}
      <button type="button" onClick={onSave} disabled={saving}
        className="w-full rounded-lg gradient-cyan py-2 text-sm font-semibold text-on-accent transition-all hover:brightness-110 disabled:opacity-40">
        {saving ? 'Saving…' : `Save ${node.name}`}
      </button>
    </div>
  );
}

function LLMConfigTab() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [selectedId, setSelectedId] = useState('agent');
  const [loadError, setLoadError] = useState('');

  const loadConfig = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      setConfig(await api.get('/api/admin/llm/config'));
    } catch (err) {
      setLoadError(err.message || 'Could not load model settings.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadConfig(); }, [loadConfig]);

  const set = (k) => (v) => setConfig((p) => ({ ...p, [k]: v }));
  const selected = MAP_NODES.find((n) => n.id === selectedId);

  const saveNode = async (node) => {
    setSaving(true);
    try {
      // Per-node partial save: only this slot's keys (+ the base trio the
      // request contract expects). Token weights are numeric in the API
      // contract; an empty/invalid field sends null so a blank input never
      // silently zeroes a cost weight — the backend's partial upsert keeps
      // the existing value instead.
      const numOrNull = (v) => {
        const n = Number(v);
        return v === '' || v === null || Number.isNaN(n) ? null : n;
      };
      const payload = {
        base_url: config.base_url, api_key: config.api_key, model: config.model,
      };
      for (const f of node.fields) {
        payload[f.key] = f.numeric ? numOrNull(config[f.key]) : config[f.key];
      }
      const updated = await api.put('/api/admin/llm/config', payload);
      setConfig(updated);
      toast.success(`${node.name} saved`);
    } catch (err) { toast.error(err.message); } finally { setSaving(false); }
  };

  if (loading) return <Spinner />;
  if (loadError || !config) return (
    <div className="p-4 md:p-8" role="alert">
      <p className="text-danger">{loadError || 'No model settings returned.'}</p>
      <button type="button" onClick={loadConfig} className="mt-3 text-cyan hover:underline">
        Retry loading settings
      </button>
    </div>
  );

  return (
    <div className="p-4 md:p-8 overflow-y-auto h-full">
      <div className="max-w-6xl mx-auto">
        <PageHeader title="Model Routing Map"
          subtitle="Changes take effect immediately — no server restart required." />
        <p className="text-xs text-cream-muted mb-4">
          <span className="text-cyan">━━</span> calls ·{' '}
          <span className="text-gold">╌╌</span> empty slot uses the default model ·
          click a node to configure it.
          {config.features && (
            <span className="ml-2">
              Research agent: <span className="text-cream-secondary">{config.features.research_agent ? 'on' : 'off'}</span>{' · '}
              PDF documents: <span className="text-cream-secondary">{config.features.pdf_documents ? 'on' : 'off'}</span>
              <span> (deployment settings)</span>
            </span>
          )}
        </p>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
          {/* Narrow screens keep the map at full size and scroll it sideways. */}
          <div className="min-w-0">
            <div className="overflow-x-auto rounded-xl border border-border-default bg-ink-deep/40">
              <div className="relative h-[470px] min-w-[720px] overflow-hidden">
                <MapEdges config={config} />
                {MAP_SOURCES.map((s) => <MapSource key={s.id} source={s} />)}
                {MAP_NODES.map((n) => (
                  <MapNode key={n.id} node={n} config={config}
                    selected={n.id === selectedId} onSelect={() => setSelectedId(n.id)} />
                ))}
              </div>
            </div>
            <p className="mt-1.5 text-[11px] text-cream-muted md:hidden">Swipe sideways to see the whole map.</p>
          </div>

          <NodePanel node={selected} config={config} set={set}
            onSave={() => saveNode(selected)} saving={saving} />
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════ MAINTENANCE TAB ═════════════════════════════ */
// [v8.0 §11D] Health panel — recent graceful-degradation counts, so silent
// fallbacks (router→rag, embedding→BM25, rerank→fusion, all_filtered) are visible.
const DEGRADATION_LABEL = {
  router: 'Router → RAG', embedding: 'Embedding → BM25-only',
  rerank: 'Rerank → fusion order', all_filtered: 'All retrieval filtered',
};

function HealthPanel() {
  const [data, setData] = useState(null);

  const load = useCallback(() => {
    api.get('/api/admin/health/degradations').then(setData).catch(() => setData(null));
  }, []);
  useEffect(() => { load(); }, [load]);

  const entries = data ? Object.entries(data.degradations || {}) : [];
  return (
    <div className="mb-6 p-5 rounded-xl bg-ink-raised border border-border-subtle">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-cream">System Health</p>
        <button onClick={load} className="text-xs text-cyan hover:underline cursor-pointer">Refresh</button>
      </div>
      <p className="mt-1 text-xs text-cream-muted">
        Fallbacks fired in the last {data?.window_minutes ?? 60} min.
      </p>
      {entries.length === 0 ? (
        <p className="mt-3 text-sm text-success">✓ No degradations — all systems nominal.</p>
      ) : (
        <ul className="mt-3 space-y-1.5">
          {entries.map(([k, n]) => (
            <li key={k} className="flex items-center justify-between text-sm">
              <span className="text-cream-secondary">{DEGRADATION_LABEL[k] || k}</span>
              <span className="font-mono text-gold">{n}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function MaintenanceTab() {
  const [days, setDays] = useState(30);
  const [pruning, setPruning] = useState(false);
  const [result, setResult] = useState(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [preview, setPreview] = useState(null); // what the prune would delete

  // Count first (dry run), so the confirmation names what will be deleted.
  const openConfirm = async () => {
    setPreview(null);
    setConfirmOpen(true);
    try {
      setPreview(await api.post('/api/admin/maintenance/prune', { older_than_days: days, dry_run: true }));
    } catch (err) {
      toast.error(err.message);
      setConfirmOpen(false);
    }
  };

  const handlePrune = async () => {
    setPruning(true);
    try {
      const res = await api.post('/api/admin/maintenance/prune', { older_than_days: days });
      setResult(res);
      toast.success(`Pruned ${plural(res.sessions_deleted, 'session')}`);
      setConfirmOpen(false);
    } catch (err) { toast.error(err.message); } finally { setPruning(false); }
  };

  return (
    <div className="p-4 md:p-8 overflow-y-auto h-full">
      <div className="max-w-md mx-auto">
        <HealthPanel />
        <PageHeader title="Database Maintenance" subtitle="Permanently remove old sessions to free disk space." />
        <div className="p-5 rounded-xl bg-danger-muted border border-danger/20 space-y-4">
          <p className="flex items-center gap-2 text-sm font-semibold text-danger">
            <AlertIcon className="w-4 h-4" /> Danger Zone — Irreversible
          </p>
          <div className="flex items-center gap-3">
            <span className="text-sm text-cream-secondary">Delete sessions older than</span>
            <input type="number" min={1} value={days} onChange={(e) => setDays(Number(e.target.value))}
              className="w-16 text-center bg-ink-deep border border-border-default text-cream text-sm px-2 py-1 rounded-lg focus:outline-none focus:border-danger/40" />
            <span className="text-sm text-cream-secondary">days</span>
          </div>
          <button onClick={openConfirm} disabled={pruning || !(days >= 1)}
            className="w-full py-2.5 rounded-lg bg-danger text-on-accent text-sm font-semibold hover:bg-danger-hover transition-all cursor-pointer disabled:opacity-40">
            Run Prune
          </button>
        </div>
        {result && (
          <div className="mt-6 p-4 rounded-xl bg-ink-raised border border-border-subtle text-sm space-y-1">
            <p className="text-cream">Sessions deleted: <span className="text-danger font-mono">{result.sessions_deleted}</span></p>
            <p className="text-cream">Messages deleted: <span className="text-danger font-mono">{result.messages_deleted}</span></p>
          </div>
        )}
      </div>

      {confirmOpen && (
        <ConfirmModal title="Prune old sessions" danger busy={pruning || !preview}
          message={preview
            ? <>Permanently delete what is older than <span className="text-cream font-medium">{plural(days, 'day')}</span>:{' '}
                <span className="text-cream font-medium">{plural(preview.sessions_deleted, 'session')}</span> with{' '}
                <span className="text-cream font-medium">{plural(preview.messages_deleted, 'message')}</span>, and{' '}
                {plural(preview.router_logs_deleted + preview.trace_logs_deleted, 'log row')}. This cannot be undone.</>
            : <>Counting what is older than {plural(days, 'day')}…</>}
          confirmLabel={pruning ? 'Pruning…' : 'Run Prune'}
          onConfirm={handlePrune} onCancel={() => setConfirmOpen(false)} />
      )}
    </div>
  );
}

/* ── Shared helpers ── */
function PageHeader({ title, subtitle, children }) {
  return (
    <div className="flex items-start justify-between gap-4 mb-6">
      <div>
        <h2 className="text-xl font-display text-cream">{title}</h2>
        {subtitle && <p className="text-xs text-cream-muted mt-1">{subtitle}</p>}
      </div>
      {children && <div className="flex items-center gap-2 shrink-0">{children}</div>}
    </div>
  );
}

function SortableTh({ label, active, dir, onClick }) {
  return (
    <th className="text-left font-medium px-4 py-3">
      <button onClick={onClick} className="inline-flex items-center gap-1 uppercase tracking-widest hover:text-cream transition-colors cursor-pointer">
        {label}
        <SortIcon className={`w-3 h-3 transition-colors ${active ? 'text-cyan' : 'text-cream-faint'}`} dir={active ? dir : null} />
      </button>
    </th>
  );
}

function IconButton({ onClick, title, danger, children }) {
  return (
    <button onClick={onClick} title={title}
      className={`p-2 rounded-lg transition-colors cursor-pointer ${
        danger ? 'text-cream-muted hover:text-danger hover:bg-danger-muted' : 'text-cream-muted hover:text-cyan hover:bg-cyan-muted'
      }`}>
      {children}
    </button>
  );
}

function SectionLabel({ children }) {
  return <p className="text-[10px] font-semibold uppercase tracking-widest text-cream-muted mb-2">{children}</p>;
}

function EmptyState({ icon, message }) {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center gap-3 text-cream-muted">
      <div className="text-cream-faint">{icon}</div>
      <p className="text-sm">{message}</p>
    </div>
  );
}

function Spinner() {
  return <div className="flex items-center justify-center h-full"><div className="w-6 h-6 rounded-full border-2 border-cyan border-t-transparent animate-[spin_0.8s_linear_infinite]" /></div>;
}

function StatCard({ label, value }) {
  return (
    <div className="bg-ink-raised rounded-xl border border-border-subtle p-4">
      <p className="text-[10px] uppercase tracking-widest text-cream-muted mb-1">{label}</p>
      <p className="text-2xl font-display text-cream">{value ?? '—'}</p>
    </div>
  );
}

function Field({ label, value, onChange, placeholder }) {
  return (
    <div className="space-y-1.5">
      <label className="mono-label">{label}</label>
      <input type="text" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
        className="w-full px-4 py-3 rounded-lg bg-ink-deep border border-border-default text-cream text-sm font-mono placeholder:text-cream-muted focus:outline-none focus:border-cyan/40 transition-colors" />
    </div>
  );
}

// Masked input for secrets (API keys). Hidden by default; each field owns its
// own reveal toggle so showing one key never exposes the others.
function SecretField({ label, value, onChange, placeholder }) {
  const [show, setShow] = useState(false);
  return (
    <div className="space-y-1.5">
      <label className="mono-label">{label}</label>
      <div className="relative">
        <input type={show ? 'text' : 'password'} value={value} onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder} autoComplete="off"
          className="w-full px-4 py-3 pr-10 rounded-lg bg-ink-deep border border-border-default text-cream text-sm placeholder:text-cream-muted focus:outline-none focus:border-cyan/40 font-mono transition-colors" />
        <button type="button" onClick={() => setShow(!show)} title={show ? 'Hide' : 'Reveal'}
          className="absolute right-3 top-1/2 -translate-y-1/2 text-cream-muted hover:text-cream cursor-pointer">
          {show ? <EyeOffIcon className="w-4 h-4" /> : <EyeIcon className="w-4 h-4" />}
        </button>
      </div>
    </div>
  );
}

function Modal({ title, children, onClose }) {
  return (
    <div className="fixed inset-0 bg-ink-deep/80 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-fade-in">
      <div className="bg-ink-base rounded-2xl border border-border-default shadow-elevated w-full max-w-md p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-display text-xl text-cream">{title}</h3>
          <button onClick={onClose} className="text-cream-muted hover:text-cream cursor-pointer text-lg leading-none">×</button>
        </div>
        {children}
      </div>
    </div>
  );
}

function ConfirmModal({ title, message, confirmLabel = 'Confirm', danger, busy, onConfirm, onCancel }) {
  return (
    <Modal title={title} onClose={onCancel}>
      <div className="space-y-5">
        <p className="text-sm text-cream-secondary leading-relaxed">{message}</p>
        <div className="flex gap-3">
          <button onClick={onCancel} disabled={busy}
            className="flex-1 py-2.5 rounded-lg bg-ink-surface border border-border-default text-cream-secondary text-sm font-medium hover:text-cream hover:bg-ink-hover transition-colors cursor-pointer disabled:opacity-40">
            Cancel
          </button>
          <button onClick={onConfirm} disabled={busy}
            className={`flex-1 py-2.5 rounded-lg text-on-accent text-sm font-semibold transition-all cursor-pointer disabled:opacity-40 ${
              danger ? 'bg-danger hover:bg-danger-hover' : 'gradient-cyan hover:brightness-110'
            }`}>
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </Modal>
  );
}

/* ── Icons (inline SVG, currentColor) ── */
function Svg({ children, className = 'w-4 h-4' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {children}
    </svg>
  );
}
function UsersIcon(p) { return <Svg {...p}><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></Svg>; }
function GridIcon(p) { return <Svg {...p}><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /></Svg>; }
function ChartIcon(p) { return <Svg {...p}><path d="M3 3v18h18" /><rect x="7" y="11" width="3" height="6" /><rect x="12" y="7" width="3" height="10" /><rect x="17" y="13" width="3" height="4" /></Svg>; }
function ChipIcon(p) { return <Svg {...p}><rect x="4" y="4" width="16" height="16" rx="2" /><rect x="9" y="9" width="6" height="6" /><path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3" /></Svg>; }
function DatabaseIcon(p) { return <Svg {...p}><ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M3 5v14a9 3 0 0 0 18 0V5" /><path d="M3 12a9 3 0 0 0 18 0" /></Svg>; }
function KeyIcon(p) { return <Svg {...p}><circle cx="7.5" cy="15.5" r="5.5" /><path d="m21 2-9.6 9.6" /><path d="m15.5 7.5 3 3L22 7l-3-3" /></Svg>; }
function TrashIcon(p) { return <Svg {...p}><path d="M3 6h18" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></Svg>; }
function UploadIcon(p) { return <Svg {...p}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="M17 8l-5-5-5 5" /><path d="M12 3v12" /></Svg>; }
function SearchIcon(p) { return <Svg {...p}><circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" /></Svg>; }
function EyeIcon(p) { return <Svg {...p}><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" /><circle cx="12" cy="12" r="3" /></Svg>; }
function EyeOffIcon(p) { return <Svg {...p}><path d="M9.88 9.88a3 3 0 1 0 4.24 4.24" /><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68" /><path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61" /><path d="m2 2 20 20" /></Svg>; }
function ChevronIcon(p) { return <Svg {...p}><path d="m9 18 6-6-6-6" /></Svg>; }
function LogoutIcon(p) { return <Svg {...p}><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="m16 17 5-5-5-5" /><path d="M21 12H9" /></Svg>; }
function AlertIcon(p) { return <Svg {...p}><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" /><path d="M12 9v4" /><path d="M12 17h.01" /></Svg>; }
function TransferIcon(p) { return <Svg {...p}><path d="m16 3 5 5-5 5" /><path d="M21 8H9" /><path d="m8 21-5-5 5-5" /><path d="M3 16h12" /></Svg>; }
function LockIcon(p) { return <Svg {...p}><rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" /></Svg>; }
function UnlockIcon(p) { return <Svg {...p}><rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 9.9-1" /></Svg>; }
function SortIcon({ className, dir }) {
  // dir: 'asc' → up emphasized, 'desc' → down emphasized, null → neutral both
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m7 4 0 16" opacity={dir === 'asc' ? 1 : 0.4} />
      <path d="m3 8 4-4 4 4" opacity={dir === 'asc' ? 1 : 0.4} />
      <path d="m17 20 0-16" opacity={dir === 'desc' ? 1 : 0.4} />
      <path d="m21 16-4 4-4-4" opacity={dir === 'desc' ? 1 : 0.4} />
    </svg>
  );
}

function MenuIcon({ className }) {
  return <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M4 6h16M4 12h16M4 18h16" /></svg>;
}

/** "1 lab", "3 labs"; many: the plural when it is not word + "s". */
function plural(n, word, many = `${word}s`) {
  return `${n} ${n === 1 ? word : many}`;
}
