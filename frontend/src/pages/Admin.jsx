/**
 * Admin.jsx — God-mode dashboard v7.2.
 * Left nav rail: Users | Oversight | Analytics | LLM Config | Maintenance.
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import toast from 'react-hot-toast';
import { useNavigate } from 'react-router-dom';
import api from '../lib/api';
import useAuthStore from '../store/authStore';

const NAV = [
  { key: 'users', label: 'Users', icon: UsersIcon },
  { key: 'oversight', label: 'Oversight', icon: GridIcon },
  { key: 'analytics', label: 'Analytics', icon: ChartIcon },
  { key: 'llm', label: 'LLM Config', icon: ChipIcon },
  { key: 'maintenance', label: 'Maintenance', icon: DatabaseIcon },
];

export default function Admin() {
  const [active, setActive] = useState('users');
  const { username, logout } = useAuthStore();
  const navigate = useNavigate();

  const handleSignOut = () => {
    logout();
    navigate('/login', { replace: true });
  };

  return (
    <div className="flex h-screen bg-ink-deep overflow-hidden">
      {/* Nav rail */}
      <aside className="w-56 shrink-0 flex flex-col border-r border-border-subtle bg-ink-base/50">
        <div className="px-5 pt-6 pb-5">
          <h1 className="font-display text-lg text-cream leading-tight">Admin Console</h1>
          <p className="text-[11px] text-cream-muted mt-0.5">God-mode oversight</p>
        </div>
        <nav className="flex-1 px-3 space-y-1">
          {NAV.map(({ key, label, icon: Icon }) => {
            const on = active === key;
            return (
              <button key={key} onClick={() => setActive(key)}
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
            <button onClick={handleSignOut} title="Sign out"
              className="p-1.5 rounded-lg text-cream-muted hover:text-danger hover:bg-danger-muted transition-colors cursor-pointer">
              <LogoutIcon className="w-4 h-4" />
            </button>
          </div>
        </div>
      </aside>

      {/* Content */}
      <main className="flex-1 overflow-hidden">
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
    <div className="p-8 overflow-y-auto h-full">
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
        <div className="rounded-xl border border-border-subtle bg-ink-raised overflow-hidden">
          <table className="w-full text-sm">
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
              className="w-full py-3 rounded-lg gradient-cyan text-cream text-sm font-semibold hover:brightness-110 transition-all cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed">
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
                className="flex-1 py-2 rounded-lg gradient-cyan text-cream text-sm font-semibold cursor-pointer hover:brightness-110 disabled:opacity-40">
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
    <div className="p-8 overflow-y-auto h-full">
      <div className="max-w-3xl mx-auto">
        {!selectedClass ? (
          /* ── Level 0: class list ── */
          <>
            <PageHeader title="Oversight" subtitle={`${classes.length} classes across the platform`} />
            {loading ? <Spinner /> : classes.length === 0 ? (
              <EmptyState icon={<GridIcon className="w-7 h-7" />} message="No classes on the platform yet." />
            ) : (
              <div className="rounded-xl border border-border-subtle bg-ink-raised overflow-hidden divide-y divide-border-subtle">
                {classes.map((cls) => (
                  <div key={cls.id} onClick={() => openClass(cls)}
                    className="group flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-ink-hover/50 transition-colors">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-cream truncate">{cls.name}</p>
                      <p className="text-[11px] text-cream-muted mt-0.5">{cls.labs?.length || 0} labs</p>
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
              subtitle={`${selectedClass.labs?.length || 0} labs · ${students.length} students`} />

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
              className="w-full py-3 rounded-lg gradient-cyan text-cream text-sm font-semibold hover:brightness-110 transition-all cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed">
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
    <div className="p-8 overflow-y-auto h-full">
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

/* ═══════════════════════════ LLM CONFIG TAB ══════════════════════════════ */
function LLMConfigTab() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.get('/api/admin/llm/config').then(setConfig).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const set = (k) => (v) => setConfig((p) => ({ ...p, [k]: v }));

  const handleSave = async () => {
    setSaving(true);
    try {
      // token weights are numeric in the API contract. An empty/invalid field
      // sends null (not 0) so a blank input never silently zeroes a cost weight —
      // the backend's partial update keeps the existing value instead.
      const numOrNull = (v) => {
        const n = Number(v);
        return v === '' || v === null || Number.isNaN(n) ? null : n;
      };
      const payload = {
        ...config,
        token_alpha: numOrNull(config.token_alpha),
        token_beta: numOrNull(config.token_beta),
      };
      const updated = await api.put('/api/admin/llm/config', payload);
      setConfig(updated);
      toast.success('LLM config saved');
    } catch (err) { toast.error(err.message); } finally { setSaving(false); }
  };

  if (loading || !config) return <Spinner />;

  return (
    <div className="p-8 overflow-y-auto h-full">
      <div className="max-w-4xl mx-auto">
        <PageHeader title="LLM Configuration"
          subtitle="Changes take effect immediately — no server restart required." />
        <p className="text-xs text-cream-muted mb-6">
          Empty endpoint/model fields in the split sections <span className="text-cyan">inherit the main LLM</span>.
        </p>

        <div className="grid xl:grid-cols-2 gap-5">
          {/* Main generation engine */}
          <ConfigSection title="Main LLM (generation)">
            <Field label="Model Name" value={config.model} onChange={set('model')} placeholder="qwen3" />
            <Field label="Base URL" value={config.base_url} onChange={set('base_url')}
              placeholder="http://localhost:11434/v1" />
            <SecretField label="API Key" value={config.api_key} onChange={set('api_key')}
              placeholder="sk-… or 'ollama'" />
          </ConfigSection>

          {/* Embedding */}
          <ConfigSection title="Embedding (RAG + router kNN)">
            <Field label="Model Name" value={config.embedding_model} onChange={set('embedding_model')}
              placeholder="BAAI/bge-m3" />
            <Field label="Base URL" value={config.embedding_url} onChange={set('embedding_url')}
              placeholder="inherits main LLM" />
            <SecretField label="API Key" value={config.embedding_api_key} onChange={set('embedding_api_key')}
              placeholder="inherits main LLM" />
          </ConfigSection>

          {/* Rerank */}
          <ConfigSection title="Reranker (empty URL = disabled)"
            desc="Base URL must be a real rerank endpoint that accepts {query, documents} — e.g. …/v1/rerank, not the chat …/v1 base (that 404s). If empty or failing, retrieval safely falls back to fusion-only ordering.">
            <Field label="Model Name" value={config.rerank_model} onChange={set('rerank_model')}
              placeholder="BAAI/bge-reranker-v2-m3" />
            <Field label="Base URL" value={config.rerank_url} onChange={set('rerank_url')}
              placeholder="https://…/v1/rerank" />
            <SecretField label="API Key" value={config.rerank_api_key} onChange={set('rerank_api_key')}
              placeholder="inherits main LLM" />
          </ConfigSection>

          {/* Ingestion (off-peak worker) */}
          <ConfigSection title="Ingestion model (off-peak worker)">
            <Field label="Model Name" value={config.ingest_model} onChange={set('ingest_model')}
              placeholder="inherits main LLM — e.g. qwen3:30b" />
            <Field label="Base URL" value={config.ingest_base_url} onChange={set('ingest_base_url')}
              placeholder="inherits main LLM" />
            <SecretField label="API Key" value={config.ingest_api_key} onChange={set('ingest_api_key')}
              placeholder="inherits main LLM" />
          </ConfigSection>

          {/* Router = ALL live auxiliary calls (router / self-eval / rewrite) */}
          <ConfigSection title="Auxiliary model (router · self-eval · rewrite)"
            desc="All live auxiliary calls share this slot. Point it at a small fast model (e.g. a 9B).">
            <Field label="Model Name" value={config.router_model} onChange={set('router_model')}
              placeholder="inherits main LLM — e.g. ministral-8b" />
            <Field label="Base URL" value={config.router_base_url} onChange={set('router_base_url')}
              placeholder="inherits main LLM" />
            <SecretField label="API Key" value={config.router_api_key} onChange={set('router_api_key')}
              placeholder="inherits main LLM" />
            {!config.router_model && (
              <p className="text-xs text-amber-400 mt-1">
                ⚠ Empty — auxiliary calls run on the main (big) chat model and compete
                with students for its capacity.
              </p>
            )}
          </ConfigSection>

          {/* [v7.3] Hint generator (answer → tiered hints, off-peak) */}
          <ConfigSection title="Hint generator (answer → tiered hints, off-peak)"
            desc="Runs off-peak on the worker — point it at the BIG model (quality over latency).">
            <Field label="Model Name" value={config.hint_model} onChange={set('hint_model')}
              placeholder="inherits main LLM" />
            <Field label="Base URL" value={config.hint_base_url} onChange={set('hint_base_url')}
              placeholder="inherits main LLM" />
            <SecretField label="API Key" value={config.hint_api_key} onChange={set('hint_api_key')}
              placeholder="inherits main LLM" />
            <Field label="Max derivation samples / exercise" value={config.hint_max_samples}
              onChange={set('hint_max_samples')} placeholder="4" />
            <Field label="Token budget / document" value={config.ingest_token_budget}
              onChange={set('ingest_token_budget')} placeholder="200000" />
          </ConfigSection>

          {/* [v8.1] Context cap — OOM protection for the local engine */}
          <ConfigSection title="Chat history context cap"
            desc="Estimated-token budget for the history sent per message (oldest turns are silently forgotten). Protects a local engine from OOM on marathon conversations. Keep it below the engine's max context length.">
            <Field label="Max history tokens (estimated)" value={config.context_max_tokens}
              onChange={set('context_max_tokens')} placeholder="8000" />
          </ConfigSection>

          {/* Token cost weights */}
          <ConfigSection title="Token quota weights (billed = prompt·α + completion·β)">
            <Field label="α — prefill weight" value={config.token_alpha} onChange={set('token_alpha')}
              placeholder="0.2" />
            <Field label="β — decode weight" value={config.token_beta} onChange={set('token_beta')}
              placeholder="1.0" />
            <Field label="Rerank score threshold (empty = gate off)"
              value={config.rerank_score_threshold} onChange={set('rerank_score_threshold')}
              placeholder="empty until calibrated — e.g. 0.35" />
          </ConfigSection>
        </div>

        <button onClick={handleSave} disabled={saving}
          className="mt-6 w-full max-w-sm py-3 rounded-lg gradient-cyan text-cream text-sm font-semibold hover:brightness-110 transition-all cursor-pointer disabled:opacity-40">
          {saving ? 'Saving…' : 'Save Configuration'}
        </button>
      </div>
    </div>
  );
}

function ConfigSection({ title, desc, children }) {
  return (
    <div className="space-y-4 border border-border-default rounded-xl p-4 bg-ink-deep/30">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-cyan/80">{title}</h3>
      {desc && <p className="text-xs text-cream-muted -mt-2 leading-relaxed">{desc}</p>}
      {children}
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
        <p className="mt-3 text-sm text-emerald-300">✓ No degradations — all systems nominal.</p>
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

  const handlePrune = async () => {
    setPruning(true);
    try {
      const res = await api.post('/api/admin/maintenance/prune', { days });
      setResult(res);
      toast.success(`Pruned ${res.sessions_deleted} sessions`);
      setConfirmOpen(false);
    } catch (err) { toast.error(err.message); } finally { setPruning(false); }
  };

  return (
    <div className="p-8 overflow-y-auto h-full">
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
          <button onClick={() => setConfirmOpen(true)} disabled={pruning}
            className="w-full py-2.5 rounded-lg bg-danger text-cream text-sm font-semibold hover:bg-danger-hover transition-all cursor-pointer disabled:opacity-40">
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
        <ConfirmModal title="Prune old sessions" danger busy={pruning}
          message={<>Permanently delete all sessions and messages older than <span className="text-cream font-medium">{days} days</span>? This cannot be undone.</>}
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
            className={`flex-1 py-2.5 rounded-lg text-cream text-sm font-semibold transition-all cursor-pointer disabled:opacity-40 ${
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
