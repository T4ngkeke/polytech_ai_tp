/**
 * Admin.jsx — God-mode dashboard v6. 5 tabs: Users | Oversight | Analytics | LLM Config | Maintenance
 */
import { useState, useEffect, useCallback } from 'react';
import toast from 'react-hot-toast';
import { useNavigate } from 'react-router-dom';
import api from '../lib/api';
import useAuthStore from '../store/authStore';
import HierarchicalSidebar from '../components/HierarchicalSidebar';

const TABS = ['Users', 'Oversight', 'Analytics', 'LLM Config', 'Maintenance'];

export default function Admin() {
  const [activeTab, setActiveTab] = useState(0);
  const { username, logout } = useAuthStore();
  const navigate = useNavigate();

  const handleSignOut = () => {
    logout();
    navigate('/login', { replace: true });
  };

  return (
    <div className="flex flex-col h-screen bg-ink-deep overflow-hidden">
      {/* Header */}
      <div className="shrink-0 px-8 pt-6 border-b border-border-subtle bg-ink-base/60">
        <div className="flex justify-between items-start">
          <div>
            <h1 className="font-display text-2xl text-cream mb-1">Admin Console</h1>
            <p className="text-xs text-cream-muted mb-4">God-mode platform oversight</p>
          </div>
          <div className="flex items-center gap-4">
            <span className="text-sm font-semibold text-cream">{username || 'Admin'}</span>
            <button onClick={handleSignOut} className="px-3 py-1.5 rounded-lg bg-danger-deep text-danger border border-danger/20 hover:bg-danger-muted transition-colors text-xs font-medium cursor-pointer">
              Sign Out
            </button>
          </div>
        </div>
        <div className="flex gap-6">
          {TABS.map((tab, i) => (
            <button key={tab} onClick={() => setActiveTab(i)}
              className={`pb-3 text-sm font-medium border-b-2 transition-all cursor-pointer ${
                activeTab === i ? 'border-cyan text-cyan' : 'border-transparent text-cream-secondary hover:text-cream'
              }`}>
              {tab}
            </button>
          ))}
        </div>
      </div>
      <div className="flex-1 overflow-hidden">
        {activeTab === 0 && <UsersTab />}
        {activeTab === 1 && <OversightTab />}
        {activeTab === 2 && <AnalyticsTab />}
        {activeTab === 3 && <LLMConfigTab />}
        {activeTab === 4 && <MaintenanceTab />}
      </div>
    </div>
  );
}

/* ═══════════════════════════════ USERS TAB ═══════════════════════════════ */
function UsersTab() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCsvModal, setShowCsvModal] = useState(false);
  const [csvFile, setCsvFile] = useState(null);
  const [csvPreview, setCsvPreview] = useState(null);
  const [importing, setImporting] = useState(false);

  const load = useCallback(async () => {
    try { setLoading(true); setUsers(await api.get('/api/admin/users')); }
    catch { toast.error('Failed to load users'); } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleRoleChange = async (userId, role) => {
    try {
      const u = await api.put(`/api/admin/users/${userId}/role`, { role });
      setUsers((p) => p.map((x) => x.id === userId ? { ...x, role: u.role } : x));
    } catch (err) { toast.error(err.message); }
  };

  const handleQuotaChange = async (userId, quota) => {
    try {
      const u = await api.put(`/api/admin/users/${userId}/quota`, { daily_token_quota: Number(quota) });
      setUsers((p) => p.map((x) => x.id === userId ? { ...x, daily_token_quota: u.daily_token_quota } : x));
    } catch (err) { toast.error(err.message); }
  };

  const handleDelete = async (userId, username) => {
    if (!confirm(`Delete user "${username}"?`)) return;
    try {
      await api.delete(`/api/admin/users/${userId}`);
      setUsers((p) => p.filter((x) => x.id !== userId));
      toast.success('User deleted');
    } catch (err) { toast.error(err.message); }
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
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-lg font-semibold text-cream">Users ({users.length})</h2>
        <button onClick={() => setShowCsvModal(true)}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-cyan-muted text-cyan border border-cyan/20 text-sm font-medium hover:bg-cyan/20 transition-all cursor-pointer">
          📥 Import CSV
        </button>
      </div>

      <div className="space-y-2 max-w-4xl">
        {users.map((u) => (
          <div key={u.id} className="flex items-center gap-4 px-4 py-3 rounded-xl bg-ink-raised border border-border-subtle">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-cream truncate">{u.username}</p>
            </div>
            <select value={u.role} onChange={(e) => handleRoleChange(u.id, e.target.value)}
              disabled={u.role === 'admin'}
              className="bg-ink-surface border border-border-default text-cream-secondary text-xs px-2 py-1 rounded-lg cursor-pointer focus:outline-none focus:border-cyan/40 disabled:opacity-50 disabled:cursor-not-allowed">
              <option value="student">student</option>
              <option value="teacher">teacher</option>
              {u.role === 'admin' && <option value="admin">admin</option>}
            </select>
            <div className="flex items-center gap-1">
              <input type="number" defaultValue={u.daily_token_quota} min={0} step={1000}
                onBlur={(e) => handleQuotaChange(u.id, e.target.value)}
                className="w-24 bg-ink-surface border border-border-default text-cream-secondary text-xs px-2 py-1 rounded-lg focus:outline-none focus:border-cyan/40" />
              <span className="text-[10px] text-cream-muted">tok/day</span>
            </div>
            <button onClick={() => handleDelete(u.id, u.username)}
              className="text-xs text-danger hover:bg-danger-muted px-3 py-1 rounded-lg transition-colors cursor-pointer">🗑️</button>
          </div>
        ))}
      </div>

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
function OversightTab() {
  const [classes, setClasses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedClass, setSelectedClass] = useState(null);
  const [selectedLab, setSelectedLab] = useState(null);
  const [students, setStudents] = useState([]);
  const [labs, setLabs] = useState([]);

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

  const handleSelectLab = (labId, classId) => {
    const cls = classes.find((c) => c.id === classId);
    const lab = cls?.labs?.find((l) => l.id === labId);
    setSelectedClass(cls); setSelectedLab(lab); setStudents([]);
    api.get(`/api/admin/classes/${classId}/students`).then(setStudents).catch(() => {});
  };

  const handleClassAction = async (action, cls) => {
    if (action === 'delete') {
      if (!confirm(`Force-delete class "${cls.name}"? This cannot be undone.`)) return;
      try {
        await api.delete(`/api/admin/classes/${cls.id}`);
        setClasses((p) => p.filter((c) => c.id !== cls.id));
        if (selectedClass?.id === cls.id) { setSelectedClass(null); setSelectedLab(null); }
        toast.success('Class deleted');
      } catch (err) { toast.error(err.message); }
    } else if (action === 'transfer') {
      const newTeacherId = prompt('Enter new teacher user ID:');
      if (!newTeacherId) return;
      try {
        const updated = await api.put(`/api/admin/classes/${cls.id}/transfer`, { teacher_id: newTeacherId });
        setClasses((p) => p.map((c) => c.id === cls.id ? { ...c, teacher_id: updated.teacher_id } : c));
        toast.success('Class transferred');
      } catch (err) { toast.error(err.message); }
    }
  };

  const handleLabAction = async (action, lab) => {
    if (action === 'delete') {
      if (!confirm(`Force-delete lab "${lab.name}"?`)) return;
      try {
        await api.delete(`/api/admin/labs/${lab.id}`);
        setClasses((p) => p.map((c) => ({ ...c, labs: c.labs?.filter((l) => l.id !== lab.id) })));
        if (selectedLab?.id === lab.id) setSelectedLab(null);
        toast.success('Lab deleted');
      } catch (err) { toast.error(err.message); }
    } else if (action === 'toggle') {
      try {
        const updated = await api.put(`/api/teacher/labs/${lab.id}`, { is_active: !lab.is_active });
        setClasses((p) => p.map((c) => ({ ...c, labs: c.labs?.map((l) => l.id === lab.id ? updated : l) })));
        toast.success(updated.is_active ? 'Lab unlocked' : 'Lab locked');
      } catch (err) { toast.error(err.message); }
    }
  };

  return (
    <div className="flex h-full overflow-hidden">
      <div className="w-64 shrink-0 border-r border-border-subtle">
        <HierarchicalSidebar role="admin" classes={classes} selectedLabId={selectedLab?.id}
          onSelectLab={handleSelectLab} onClassAction={handleClassAction} onLabAction={handleLabAction} loading={loading} />
      </div>
      <div className="flex-1 p-8 overflow-y-auto">
        {selectedClass && (
          <div className="space-y-4 max-w-2xl">
            <h2 className="text-lg font-semibold text-cream">{selectedClass.name}</h2>
            {selectedLab && <p className="text-sm text-cyan font-mono">› {selectedLab.name}</p>}
            {students.length > 0 && (
              <div>
                <p className="text-xs text-cream-muted uppercase tracking-widest mb-2">Students ({students.length})</p>
                <div className="space-y-1">
                  {students.map((s) => (
                    <div key={s.id} className="flex justify-between px-3 py-2 rounded-lg bg-ink-raised border border-border-subtle text-sm">
                      <span className="text-cream">{s.username}</span>
                      <span className="text-cream-muted font-mono text-xs">{s.role}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
        {!selectedClass && <p className="text-cream-muted text-sm">Select a class or lab from the sidebar.</p>}
      </div>
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
    <div className="p-8 overflow-y-auto h-full space-y-6 max-w-4xl">
      {global && (
        <div className="grid grid-cols-2 gap-4 mb-6">
          <StatCard label="Platform Tokens" value={global.total_tokens?.toLocaleString()} />
          <StatCard label="Platform Requests" value={global.total_requests?.toLocaleString()} />
        </div>
      )}
      <h3 className="text-sm font-semibold text-cream">Class Breakdown</h3>
      {classes.map((cls) => {
        const data = classAnalytics[cls.id];
        return (
          <div key={cls.id} className="bg-ink-raised rounded-xl border border-border-subtle overflow-hidden">
            <button onClick={() => loadClassAnalytics(cls.id)}
              className="w-full flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-ink-hover transition-colors">
              <span className="text-sm font-medium text-cream">{cls.name}</span>
              <div className="flex items-center gap-3">
                {data && <span className="text-xs font-mono text-cyan">{data.total_tokens?.toLocaleString()} tok</span>}
                <span className={`text-cream-muted transition-transform ${expanded[cls.id] ? 'rotate-90' : ''}`}>›</span>
              </div>
            </button>
            {expanded[cls.id] && data && (
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
  );
}

/* ═══════════════════════════ LLM CONFIG TAB ══════════════════════════════ */
function LLMConfigTab() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showKey, setShowKey] = useState(false);

  useEffect(() => {
    api.get('/api/admin/llm/config').then(setConfig).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const set = (k) => (v) => setConfig((p) => ({ ...p, [k]: v }));

  const handleSave = async () => {
    setSaving(true);
    try {
      // token weights are numeric in the API contract.
      const payload = {
        ...config,
        token_alpha: Number(config.token_alpha),
        token_beta: Number(config.token_beta),
      };
      const updated = await api.put('/api/admin/llm/config', payload);
      setConfig(updated);
      toast.success('LLM config saved');
    } catch (err) { toast.error(err.message); } finally { setSaving(false); }
  };

  if (loading || !config) return <Spinner />;

  return (
    <div className="p-8 overflow-y-auto h-full">
      <div className="max-w-lg space-y-6">
        <div>
          <h2 className="text-lg font-semibold text-cream mb-2">LLM Configuration</h2>
          <p className="text-xs text-cream-muted">Changes take effect immediately — no server restart required.
            Empty endpoint/model fields in the split sections <span className="text-cyan">inherit the main LLM</span>.</p>
        </div>

        {/* Main generation engine */}
        <ConfigSection title="Main LLM (generation)">
          <Field label="Base URL" value={config.base_url} onChange={set('base_url')}
            placeholder="http://localhost:11434/v1" />
          <Field label="Model Name" value={config.model} onChange={set('model')} placeholder="qwen3" />
          <div className="space-y-1.5">
            <label className="mono-label">API Key</label>
            <div className="relative">
              <input type={showKey ? 'text' : 'password'} value={config.api_key}
                onChange={(e) => set('api_key')(e.target.value)} placeholder="sk-… or 'ollama'"
                className="w-full px-4 py-3 pr-10 rounded-lg bg-ink-deep border border-border-default text-cream text-sm placeholder:text-cream-muted focus:outline-none focus:border-cyan/40 font-mono transition-colors" />
              <button onClick={() => setShowKey(!showKey)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-cream-muted hover:text-cream cursor-pointer text-xs">
                {showKey ? '🙈' : '👁️'}
              </button>
            </div>
          </div>
        </ConfigSection>

        {/* Embedding */}
        <ConfigSection title="Embedding (RAG + router kNN)">
          <Field label="Model Name" value={config.embedding_model} onChange={set('embedding_model')}
            placeholder="BAAI/bge-m3" />
          <Field label="Base URL" value={config.embedding_url} onChange={set('embedding_url')}
            placeholder="inherits main LLM" />
          <Field label="API Key" value={config.embedding_api_key} onChange={set('embedding_api_key')}
            placeholder="inherits main LLM" />
        </ConfigSection>

        {/* Rerank */}
        <ConfigSection title="Reranker (empty URL = disabled)">
          <Field label="URL" value={config.rerank_url} onChange={set('rerank_url')}
            placeholder="http://rerank:8080/rerank" />
          <Field label="Model Name" value={config.rerank_model} onChange={set('rerank_model')}
            placeholder="BAAI/bge-reranker-v2-m3" />
          <Field label="API Key" value={config.rerank_api_key} onChange={set('rerank_api_key')}
            placeholder="inherits main LLM" />
        </ConfigSection>

        {/* Ingestion (off-peak worker) */}
        <ConfigSection title="Ingestion model (off-peak worker)">
          <Field label="Model Name" value={config.ingest_model} onChange={set('ingest_model')}
            placeholder="inherits main LLM — e.g. qwen3:30b" />
          <Field label="Base URL" value={config.ingest_base_url} onChange={set('ingest_base_url')}
            placeholder="inherits main LLM" />
          <Field label="API Key" value={config.ingest_api_key} onChange={set('ingest_api_key')}
            placeholder="inherits main LLM" />
        </ConfigSection>

        {/* Router (live exercise-number fallback) */}
        <ConfigSection title="Router model (live exercise fallback)">
          <Field label="Model Name" value={config.router_model} onChange={set('router_model')}
            placeholder="inherits main LLM — e.g. qwen3:30b" />
          <Field label="Base URL" value={config.router_base_url} onChange={set('router_base_url')}
            placeholder="inherits main LLM" />
          <Field label="API Key" value={config.router_api_key} onChange={set('router_api_key')}
            placeholder="inherits main LLM" />
        </ConfigSection>

        {/* Token cost weights */}
        <ConfigSection title="Token quota weights (billed = prompt·α + completion·β)">
          <Field label="α — prefill weight" value={config.token_alpha} onChange={set('token_alpha')}
            placeholder="0.2" />
          <Field label="β — decode weight" value={config.token_beta} onChange={set('token_beta')}
            placeholder="1.0" />
        </ConfigSection>

        <button onClick={handleSave} disabled={saving}
          className="w-full py-3 rounded-lg gradient-cyan text-cream text-sm font-semibold hover:brightness-110 transition-all cursor-pointer disabled:opacity-40">
          {saving ? 'Saving…' : 'Save Configuration'}
        </button>
      </div>
    </div>
  );
}

function ConfigSection({ title, children }) {
  return (
    <div className="space-y-4 border border-border-default rounded-xl p-4 bg-ink-deep/30">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-cyan/80">{title}</h3>
      {children}
    </div>
  );
}

/* ═══════════════════════════ MAINTENANCE TAB ═════════════════════════════ */
function MaintenanceTab() {
  const [days, setDays] = useState(30);
  const [pruning, setPruning] = useState(false);
  const [result, setResult] = useState(null);

  const handlePrune = async () => {
    if (!confirm(`Hard-delete ALL sessions older than ${days} days? This is PERMANENT.`)) return;
    setPruning(true); setResult(null);
    try {
      const res = await api.post('/api/admin/maintenance/prune', { older_than_days: days });
      setResult(res);
      toast.success(`Pruned ${res.sessions_deleted} sessions`);
    } catch (err) { toast.error(err.message); } finally { setPruning(false); }
  };

  return (
    <div className="p-8 overflow-y-auto h-full">
      <div className="max-w-md space-y-6">
        <div>
          <h2 className="text-lg font-semibold text-cream mb-1">Database Maintenance</h2>
          <p className="text-xs text-cream-muted">Permanently remove old sessions to free disk space.</p>
        </div>
        <div className="p-5 rounded-xl bg-danger-muted border border-danger/20 space-y-4">
          <p className="text-sm font-semibold text-danger">⚠️ Danger Zone — Irreversible</p>
          <div className="flex items-center gap-3">
            <span className="text-sm text-cream-secondary">Delete sessions older than</span>
            <input type="number" min={1} value={days} onChange={(e) => setDays(Number(e.target.value))}
              className="w-16 text-center bg-ink-deep border border-border-default text-cream text-sm px-2 py-1 rounded-lg focus:outline-none focus:border-danger/40" />
            <span className="text-sm text-cream-secondary">days</span>
          </div>
          <button onClick={handlePrune} disabled={pruning}
            className="w-full py-2.5 rounded-lg bg-danger text-cream text-sm font-semibold hover:bg-danger-hover transition-all cursor-pointer disabled:opacity-40">
            {pruning ? 'Pruning…' : 'Run Prune'}
          </button>
        </div>
        {result && (
          <div className="p-4 rounded-xl bg-ink-raised border border-border-subtle text-sm space-y-1">
            <p className="text-cream">Sessions deleted: <span className="text-danger font-mono">{result.sessions_deleted}</span></p>
            <p className="text-cream">Messages deleted: <span className="text-danger font-mono">{result.messages_deleted}</span></p>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Shared helpers ── */
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
