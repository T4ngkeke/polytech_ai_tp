/**
 * Teacher.jsx — Teacher workspace v6.
 * Layout: Left HierarchicalSidebar + Right context-sensitive panel.
 * Selected node determines right panel: Class (students/rules) | Lab (rules/analytics/audit).
 */

import { useState, useEffect, useCallback } from 'react';
import toast from 'react-hot-toast';
import api from '../lib/api';
import HierarchicalSidebar from '../components/HierarchicalSidebar';

const TABS_CLASS = ['Rules', 'Analytics'];
const TABS_LAB = ['Settings', 'Rules', 'Analytics', 'Audit', 'Students'];

export default function Teacher() {
  // ── Tree state ──
  const [classes, setClasses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedClass, setSelectedClass] = useState(null);
  const [selectedLab, setSelectedLab] = useState(null);
  const [activeTab, setActiveTab] = useState(0);

  // ── Right panel data ──
  const [students, setStudents] = useState([]);
  const [rule, setRule] = useState(null);
  const [ruleText, setRuleText] = useState('');
  const [ruleActive, setRuleActive] = useState(true);
  const [analytics, setAnalytics] = useState(null);
  const [auditSessions, setAuditSessions] = useState([]);
  const [expandedStudentId, setExpandedStudentId] = useState(null);
  const [expandedSession, setExpandedSession] = useState(null);
  const [inheritedClassRule, setInheritedClassRule] = useState('');

  // ── Modals ──
  const [showCreateClass, setShowCreateClass] = useState(false);
  const [newClassName, setNewClassName] = useState('');
  const [showCreateLab, setShowCreateLab] = useState(false);
  const [newLabName, setNewLabName] = useState('');
  const [createLabClassId, setCreateLabClassId] = useState(null);
  const [showRenameClass, setShowRenameClass] = useState(false);
  const [renamingClass, setRenamingClass] = useState(null);
  const [renameClassValue, setRenameClassValue] = useState('');
  const [showRenameLab, setShowRenameLab] = useState(false);
  const [renamingLab, setRenamingLab] = useState(null);
  const [renameLabValue, setRenameLabValue] = useState('');
  
  const [studentRuleModal, setStudentRuleModal] = useState(null); // { studentId, username }
  const [studentRuleText, setStudentRuleText] = useState('');
  const [studentRuleActive, setStudentRuleActive] = useState(true);

  // ── Load classes + labs ──
  const loadClasses = useCallback(async () => {
    try {
      setLoading(true);
      const cls = await api.get('/api/teacher/classes');
      const withLabs = await Promise.all(
        cls.map(async (c) => {
          const labs = await api.get(`/api/teacher/classes/${c.id}/labs`).catch(() => []);
          return { ...c, labs };
        })
      );
      setClasses(withLabs);
    } catch { toast.error('Failed to load classes'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadClasses(); }, [loadClasses]);

  // ── Select lab from sidebar ──
  const handleSelectLab = useCallback((labId, classId) => {
    const cls = classes.find((c) => c.id === classId);
    const lab = cls?.labs?.find((l) => l.id === labId);
    setSelectedClass(cls || null);
    setSelectedLab(lab || null);
    setActiveTab(0);
    setAnalytics(null);
    setAuditSessions([]);
  }, [classes]);

  // ── When class node clicked (no lab): set selectedClass, clear lab ──
  const handleSelectClass = useCallback((cls) => {
    setSelectedClass(cls);
    setSelectedLab(null);
    setActiveTab(0);
    setStudents([]);
    setAnalytics(null);
  }, []);

  // ── Load tab data on tab change ──
  useEffect(() => {
    if (!selectedClass && !selectedLab) return;
    const level = selectedLab ? 'lab' : 'class';
    const targetId = selectedLab ? selectedLab.id : selectedClass?.id;

    if (level === 'class') {
      if (activeTab === 0) {
        api.get(`/api/teacher/rules?level=class&target_id=${selectedClass.id}`)
          .then((rules) => { const r = rules[0] || null; setRule(r); setRuleText(r?.rules_text || ''); setRuleActive(r?.is_active ?? true); })
          .catch(() => { setRule(null); setRuleText(''); });
      } else if (activeTab === 1) {
        api.get(`/api/teacher/analytics/classes/${selectedClass.id}`).then(setAnalytics).catch(() => setAnalytics(null));
      }
    } else {
      if (activeTab === 1) {
        Promise.all([
          api.get(`/api/teacher/rules?level=lab&target_id=${selectedLab.id}`),
          api.get(`/api/teacher/rules?level=class&target_id=${selectedClass.id}`)
        ])
          .then(([labRules, classRules]) => {
            const r = labRules[0] || null; 
            setRule(r); setRuleText(r?.rules_text || ''); setRuleActive(r?.is_active ?? true);
            setInheritedClassRule(classRules[0]?.rules_text || '');
          })
          .catch(() => { setRule(null); setRuleText(''); setInheritedClassRule(''); });
      } else if (activeTab === 2) {
        api.get(`/api/teacher/analytics/classes/${selectedClass.id}`).then(setAnalytics).catch(() => setAnalytics(null));
      } else if (activeTab === 3 || activeTab === 4) {
        Promise.all([
          api.get(`/api/teacher/classes/${selectedClass.id}/students`),
          api.get(`/api/teacher/chat-history?lab_id=${selectedLab.id}`)
        ]).then(([stds, sess]) => {
          setStudents(stds);
          setAuditSessions(sess);
        }).catch(() => { setStudents([]); setAuditSessions([]); });
      }
    }
  }, [activeTab, selectedClass, selectedLab]);

  // ── Handlers ──
  const handleCreateClass = async () => {
    if (!newClassName.trim()) return;
    try {
      const c = await api.post('/api/teacher/classes', { name: newClassName.trim() });
      setClasses((prev) => [{ ...c, labs: [] }, ...prev]);
      setShowCreateClass(false);
      setNewClassName('');
      toast.success(`Class "${c.name}" created`);
    } catch (err) { toast.error(err.message); }
  };

  const handleCreateLab = async () => {
    if (!newLabName.trim() || !createLabClassId) return;
    try {
      const lab = await api.post(`/api/teacher/classes/${createLabClassId}/labs`, { name: newLabName.trim() });
      setClasses((prev) => prev.map((c) => c.id === createLabClassId ? { ...c, labs: [lab, ...(c.labs || [])] } : c));
      setShowCreateLab(false);
      setNewLabName('');
      toast.success(`Lab "${lab.name}" created`);
    } catch (err) { toast.error(err.message); }
  };

  const handleClassAction = async (action, cls) => {
    if (action === 'rename') { setRenamingClass(cls); setRenameClassValue(cls.name); setShowRenameClass(true); }
    else if (action === 'delete') {
      if (!confirm(`Archive class "${cls.name}"?`)) return;
      try {
        await api.delete(`/api/teacher/classes/${cls.id}`);
        setClasses((prev) => prev.filter((c) => c.id !== cls.id));
        if (selectedClass?.id === cls.id) { setSelectedClass(null); setSelectedLab(null); }
        toast.success('Class archived');
      } catch (err) { toast.error(err.message); }
    } else if (action === 'reset-code') {
      try {
        const res = await api.post(`/api/teacher/classes/${cls.id}/reset-code`, {});
        setClasses((prev) => prev.map((c) => c.id === cls.id ? { ...c, invite_code: res.invite_code } : c));
        toast.success(`New code: ${res.invite_code}`);
      } catch (err) { toast.error(err.message); }
    }
  };

  const handleLabAction = async (action, lab) => {
    if (action === 'create') { setCreateLabClassId(lab.class_id); setShowCreateLab(true); }
    else if (action === 'rename') { setRenamingLab(lab); setRenameLabValue(lab.name); setShowRenameLab(true); }
    else if (action === 'toggle') {
      try {
        const updated = await api.put(`/api/teacher/labs/${lab.id}`, { is_active: !lab.is_active });
        setClasses((prev) => prev.map((c) => ({ ...c, labs: c.labs?.map((l) => l.id === lab.id ? updated : l) })));
        toast.success(updated.is_active ? 'Lab unlocked' : 'Lab locked');
      } catch (err) { toast.error(err.message); }
    } else if (action === 'delete') {
      if (!confirm(`Archive lab "${lab.name}"?`)) return;
      try {
        await api.delete(`/api/teacher/labs/${lab.id}`);
        setClasses((prev) => prev.map((c) => ({ ...c, labs: c.labs?.filter((l) => l.id !== lab.id) })));
        if (selectedLab?.id === lab.id) setSelectedLab(null);
        toast.success('Lab archived');
      } catch (err) { toast.error(err.message); }
    }
  };

  const handleRenameClass = async () => {
    if (!renameClassValue.trim()) return;
    try {
      const updated = await api.put(`/api/teacher/classes/${renamingClass.id}`, { name: renameClassValue.trim() });
      setClasses((prev) => prev.map((c) => c.id === updated.id ? { ...c, name: updated.name } : c));
      if (selectedClass?.id === updated.id) setSelectedClass((p) => ({ ...p, name: updated.name }));
      setShowRenameClass(false);
      toast.success('Class renamed');
    } catch (err) { toast.error(err.message); }
  };

  const handleRenameLab = async () => {
    if (!renameLabValue.trim()) return;
    try {
      const updated = await api.put(`/api/teacher/labs/${renamingLab.id}`, { name: renameLabValue.trim() });
      setClasses((prev) => prev.map((c) => ({ ...c, labs: c.labs?.map((l) => l.id === updated.id ? updated : l) })));
      if (selectedLab?.id === updated.id) setSelectedLab(updated);
      setShowRenameLab(false);
      toast.success('Lab renamed');
    } catch (err) { toast.error(err.message); }
  };

  const handleSaveRule = async () => {
    if (!ruleText.trim()) return;
    const level = selectedLab ? 'lab' : 'class';
    const targetId = selectedLab ? selectedLab.id : selectedClass?.id;
    try {
      const r = await api.put('/api/teacher/rules', { level, target_id: targetId, rules_text: ruleText, is_active: ruleActive });
      setRule(r);
      toast.success('Rule saved');
    } catch (err) { toast.error(err.message); }
  };

  const handleKickStudent = async (studentId) => {
    if (!selectedClass || !confirm('Remove this student from class?')) return;
    try {
      await api.delete(`/api/teacher/classes/${selectedClass.id}/students/${studentId}`);
      setStudents((prev) => prev.filter((s) => s.id !== studentId));
      toast.success('Student removed');
    } catch (err) { toast.error(err.message); }
  };

  const handleOpenStudentRule = async (student) => {
    setStudentRuleModal({ studentId: student.id, username: student.username });
    try {
      const rules = await api.get(`/api/teacher/rules?level=student&target_id=${student.id}`);
      const r = rules[0];
      setStudentRuleText(r?.rules_text || '');
      setStudentRuleActive(r?.is_active ?? true);
    } catch {
      setStudentRuleText('');
      setStudentRuleActive(true);
    }
  };

  const handleSaveStudentRule = async () => {
    if (!studentRuleText.trim() || !studentRuleModal) return;
    try {
      await api.put('/api/teacher/rules', {
        level: 'student', target_id: studentRuleModal.studentId, rules_text: studentRuleText, is_active: studentRuleActive
      });
      toast.success('Student rule saved');
      setStudentRuleModal(null);
    } catch (err) { toast.error(err.message); }
  };

  // ── Tabs ──
  const tabs = selectedLab ? TABS_LAB : TABS_CLASS;
  const level = selectedLab ? 'lab' : 'class';
  const context = selectedLab
    ? `${selectedClass?.name} › ${selectedLab.name}`
    : selectedClass
    ? selectedClass.name
    : null;

  return (
    <div className="flex h-screen bg-ink-deep overflow-hidden">
      {/* LEFT: Tree */}
      <div className="w-64 shrink-0">
        <HierarchicalSidebar
          role="teacher"
          classes={classes}
          selectedLabId={selectedLab?.id}
          onSelectClass={handleSelectClass}
          onSelectLab={handleSelectLab}
          onCreateClass={() => setShowCreateClass(true)}
          onClassAction={handleClassAction}
          onLabAction={handleLabAction}
          loading={loading}
        />
      </div>

      {/* RIGHT: Panel */}
      <div className="flex-1 flex flex-col min-w-0">
        {!selectedClass && !selectedLab ? (
          <EmptyState />
        ) : (
          <>
            {/* Panel header */}
            <div className="shrink-0 px-8 pt-6 pb-0 border-b border-border-subtle bg-ink-base/60">
              <p className="text-[10px] text-cream-muted uppercase tracking-widest mb-1 font-mono">{context}</p>
              <div className="flex gap-6">
                {tabs.map((tab, i) => (
                  <button
                    key={tab}
                    onClick={() => setActiveTab(i)}
                    className={`pb-3 text-sm font-medium border-b-2 transition-all cursor-pointer ${
                      activeTab === i ? 'border-cyan text-cyan' : 'border-transparent text-cream-secondary hover:text-cream'
                    }`}
                  >
                    {tab}
                  </button>
                ))}
              </div>
            </div>

            {/* Panel content */}
            <div className="flex-1 p-8 overflow-y-auto">
              {level === 'class' && activeTab === 0 && <RulesPanel ruleText={ruleText} setRuleText={setRuleText} ruleActive={ruleActive} setRuleActive={setRuleActive} onSave={handleSaveRule} context={context} inheritedRule="" />}
              {level === 'class' && activeTab === 1 && <AnalyticsPanel analytics={analytics} />}

              {level === 'lab' && activeTab === 0 && <LabSettingsPanel lab={selectedLab} onToggle={() => handleLabAction('toggle', selectedLab)} />}
              {level === 'lab' && activeTab === 1 && <RulesPanel ruleText={ruleText} setRuleText={setRuleText} ruleActive={ruleActive} setRuleActive={setRuleActive} onSave={handleSaveRule} context={context} inheritedRule={inheritedClassRule} />}
              {level === 'lab' && activeTab === 2 && <AnalyticsPanel analytics={analytics} filterLabId={selectedLab.id} />}
              {level === 'lab' && activeTab === 3 && <AuditPanel sessions={auditSessions} students={students} expandedStudentId={expandedStudentId} setExpandedStudentId={setExpandedStudentId} expandedSession={expandedSession} setExpandedSession={setExpandedSession} />}
              {level === 'lab' && activeTab === 4 && <StudentsPanel students={students} onKick={handleKickStudent} onSetRule={handleOpenStudentRule} />}
            </div>
          </>
        )}
      </div>

      {/* Modals */}
      {showCreateClass && (
        <SimpleModal title="New Class" value={newClassName} setValue={setNewClassName}
          placeholder="e.g. Algorithms 2024" onConfirm={handleCreateClass} onClose={() => setShowCreateClass(false)} confirmLabel="Create" />
      )}
      {showCreateLab && (
        <SimpleModal title="New Lab" value={newLabName} setValue={setNewLabName}
          placeholder="e.g. Python Lab 1" onConfirm={handleCreateLab} onClose={() => setShowCreateLab(false)} confirmLabel="Create" />
      )}
      {showRenameClass && (
        <SimpleModal title="Rename Class" value={renameClassValue} setValue={setRenameClassValue}
          placeholder="" onConfirm={handleRenameClass} onClose={() => setShowRenameClass(false)} confirmLabel="Rename" />
      )}
      {showRenameLab && (
        <SimpleModal title="Rename Lab" value={renameLabValue} setValue={setRenameLabValue}
          placeholder="" onConfirm={handleRenameLab} onClose={() => setShowRenameLab(false)} confirmLabel="Rename" />
      )}
      {studentRuleModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-ink-surface border border-border-default rounded-2xl w-full max-w-md p-6 shadow-elevated animate-scale-in">
            <h3 className="text-lg font-semibold text-cream mb-1">Student Rule</h3>
            <p className="text-xs text-cream-muted mb-4">Prompt injected only for <span className="text-cyan font-medium">{studentRuleModal.username}</span></p>
            <textarea
              value={studentRuleText}
              onChange={(e) => setStudentRuleText(e.target.value)}
              rows={5}
              placeholder="e.g. 'Explain concepts using simpler terms...'"
              className="w-full px-4 py-3 rounded-xl bg-ink-deep border border-border-default text-cream text-sm font-mono placeholder:text-cream-muted focus:outline-none focus:border-cyan/40 resize-none transition-colors mb-4"
            />
            <div className="flex items-center justify-between mb-6">
              <label className="flex items-center gap-2 cursor-pointer">
                <div onClick={() => setStudentRuleActive(!studentRuleActive)} className={`w-9 h-5 rounded-full transition-colors ${studentRuleActive ? 'bg-cyan' : 'bg-ink-base'} relative`}>
                  <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-cream shadow transition-all ${studentRuleActive ? 'left-4' : 'left-0.5'}`} />
                </div>
                <span className="text-xs text-cream-secondary">{studentRuleActive ? 'Active' : 'Disabled'}</span>
              </label>
            </div>
            <div className="flex gap-3 justify-end">
              <button onClick={() => setStudentRuleModal(null)} className="px-4 py-2 rounded-lg text-sm font-medium text-cream-secondary hover:text-cream hover:bg-ink-hover transition-colors">Cancel</button>
              <button onClick={handleSaveStudentRule} className="px-4 py-2 rounded-lg gradient-cyan text-cream text-sm font-semibold hover:brightness-110 transition-colors">Save</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Sub-panels ── */

function EmptyState() {
  return (
    <div className="flex items-center justify-center h-full">
      <div className="text-center max-w-xs">
        <div className="w-16 h-16 rounded-2xl gradient-cyan mx-auto mb-4 flex items-center justify-center shadow-glow">
          <svg className="w-8 h-8 text-cream" fill="none" stroke="currentColor" strokeWidth="1.5" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 10h16M4 14h8M4 18h8" />
          </svg>
        </div>
        <h3 className="font-display text-xl text-cream mb-2">Select a node</h3>
        <p className="text-sm text-cream-secondary">Click a class or lab in the sidebar to manage it.</p>
      </div>
    </div>
  );
}

function StudentsPanel({ students, onKick, onSetRule }) {
  if (students.length === 0) return <p className="text-cream-muted text-sm">No students enrolled yet.</p>;
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-cream mb-4">Enrolled Students ({students.length})</h3>
      {students.map((s) => (
        <div key={s.id} className="flex items-center justify-between px-4 py-3 rounded-xl bg-ink-raised border border-border-subtle">
          <div>
            <p className="text-sm font-medium text-cream">{s.username}</p>
            <p className="text-xs text-cream-muted font-mono">{s.role}</p>
          </div>
          <div className="flex gap-2">
            <button onClick={() => onSetRule(s)} className="text-xs text-cyan hover:bg-cyan-muted px-3 py-1 rounded-lg transition-colors cursor-pointer border border-cyan/20">
              Set Rule
            </button>
            <button onClick={() => onKick(s.id)} className="text-xs text-danger hover:bg-danger-muted px-3 py-1 rounded-lg transition-colors cursor-pointer border border-transparent">
              Kick
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

function RulesPanel({ ruleText, setRuleText, ruleActive, setRuleActive, onSave, context, inheritedRule }) {
  return (
    <div className="max-w-2xl space-y-4">
      <div>
        <h3 className="text-sm font-semibold text-cream mb-1">Prompt Rules</h3>
        <p className="text-xs text-cream-muted mb-4">Target: <span className="text-cyan font-mono">{context}</span></p>
      </div>

      {inheritedRule && (
        <div className="mb-4">
          <p className="text-[10px] text-cream-muted uppercase tracking-widest mb-2 font-semibold">Inherited Class Rule</p>
          <div className="w-full px-4 py-3 rounded-xl bg-ink-raised border border-border-subtle text-cream-secondary text-xs font-mono opacity-70">
            {inheritedRule}
          </div>
        </div>
      )}

      <p className="text-[10px] text-cream-muted uppercase tracking-widest mb-2 font-semibold">Current Level Rule</p>
      <textarea
        value={ruleText}
        onChange={(e) => setRuleText(e.target.value)}
        rows={10}
        placeholder="Enter system prompt rules… e.g. 'Only answer in French. Do not discuss topics unrelated to Python programming.'"
        className="w-full px-4 py-3 rounded-xl bg-ink-deep border border-border-default text-cream text-sm font-mono placeholder:text-cream-muted focus:outline-none focus:border-cyan/40 resize-none transition-colors"
      />
      <div className="flex items-center justify-between">
        <label className="flex items-center gap-2 cursor-pointer">
          <div
            onClick={() => setRuleActive(!ruleActive)}
            className={`w-9 h-5 rounded-full transition-colors ${ruleActive ? 'bg-cyan' : 'bg-ink-surface'} relative`}
          >
            <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-cream shadow transition-all ${ruleActive ? 'left-4' : 'left-0.5'}`} />
          </div>
          <span className="text-xs text-cream-secondary">{ruleActive ? 'Active' : 'Disabled'}</span>
        </label>
        <button onClick={onSave} disabled={!ruleText.trim()}
          className="px-5 py-2 rounded-lg gradient-cyan text-cream text-sm font-semibold hover:brightness-110 transition-all cursor-pointer disabled:opacity-40">
          Save Rule
        </button>
      </div>
    </div>
  );
}

function LabSettingsPanel({ lab, onToggle }) {
  return (
    <div className="max-w-md space-y-4">
      <h3 className="text-sm font-semibold text-cream mb-4">Lab Settings</h3>
      <div className="flex items-center justify-between px-4 py-4 rounded-xl bg-ink-raised border border-border-subtle">
        <div>
          <p className="text-sm font-medium text-cream">Lab Status</p>
          <p className="text-xs text-cream-muted">{lab.is_active ? 'Students can chat in this lab' : 'Lab is locked — no new messages'}</p>
        </div>
        <button onClick={onToggle}
          className={`px-4 py-2 rounded-lg text-xs font-semibold transition-all cursor-pointer ${
            lab.is_active ? 'bg-danger-muted text-danger hover:bg-danger-muted/80' : 'bg-success-muted text-success hover:bg-success-muted/80'
          }`}>
          {lab.is_active ? '🔒 Lock' : '🔓 Unlock'}
        </button>
      </div>
    </div>
  );
}

function AnalyticsPanel({ analytics, filterLabId }) {
  if (!analytics) return (
    <div className="flex items-center gap-2 text-cream-muted text-sm">
      <div className="w-4 h-4 rounded-full border-2 border-cyan border-t-transparent animate-[spin_0.8s_linear_infinite]" />
      Loading analytics…
    </div>
  );

  const labs = filterLabId ? analytics.labs?.filter((l) => l.lab_id === filterLabId) : analytics.labs;

  return (
    <div className="space-y-6 max-w-3xl">
      <div className="grid grid-cols-2 gap-4">
        <StatCard label="Total Tokens" value={analytics.total_tokens?.toLocaleString()} />
        <StatCard label="Total Requests" value={analytics.total_requests?.toLocaleString()} />
      </div>
      {labs?.map((lab) => (
        <div key={lab.lab_id} className="bg-ink-raised rounded-xl border border-border-subtle p-4">
          <div className="flex items-center justify-between mb-3">
            <h4 className="text-sm font-semibold text-cream">{lab.lab_name}</h4>
            <span className="text-xs font-mono text-cyan">{lab.tokens?.toLocaleString()} tokens</span>
          </div>
          <div className="space-y-2">
            {lab.students?.map((s) => (
              <div key={s.user_id} className="flex items-center justify-between text-xs">
                <span className="text-cream-secondary">{s.username}</span>
                <div className="flex items-center gap-4 text-cream-muted font-mono">
                  <span>{s.tokens_used?.toLocaleString()} tok</span>
                  <span>{s.request_count} req</span>
                </div>
              </div>
            ))}
            {(!lab.students || lab.students.length === 0) && (
              <p className="text-xs text-cream-muted italic">No activity yet</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function AuditPanel({ sessions, students, expandedStudentId, setExpandedStudentId, expandedSession, setExpandedSession }) {
  if (!students || students.length === 0) return <p className="text-cream-muted text-sm">No students in this class yet.</p>;
  return (
    <div className="space-y-3 max-w-3xl">
      <h3 className="text-sm font-semibold text-cream mb-4">Chat Audit</h3>
      {students.map((student) => {
        const studentSessions = sessions.filter(s => s.user_id === student.id);
        const isStudentExpanded = expandedStudentId === student.id;
        
        return (
          <div key={student.id} className="bg-ink-raised rounded-xl border border-border-subtle overflow-hidden">
            <button
              onClick={() => setExpandedStudentId(isStudentExpanded ? null : student.id)}
              className="w-full flex items-center justify-between px-4 py-3 text-left cursor-pointer hover:bg-ink-hover transition-colors"
            >
              <div>
                <p className="text-sm font-medium text-cream">{student.username}</p>
                <p className="text-xs text-cream-muted font-mono">{studentSessions.length} sessions</p>
              </div>
              <span className={`text-cream-muted transition-transform ${isStudentExpanded ? 'rotate-90' : ''}`}>›</span>
            </button>
            
            {isStudentExpanded && (
              <div className="border-t border-border-subtle bg-ink-base">
                {studentSessions.length === 0 ? (
                  <p className="text-xs text-cream-muted p-4 italic">No chat history for this student.</p>
                ) : (
                  studentSessions.map((s) => (
                    <div key={s.id} className="border-b border-border-subtle last:border-0">
                      <button
                        onClick={() => setExpandedSession(expandedSession === s.id ? null : s.id)}
                        className="w-full flex items-center justify-between px-4 py-2 text-left cursor-pointer hover:bg-ink-hover transition-colors bg-ink-deep"
                      >
                        <div>
                          <p className="text-xs font-medium text-cream">{s.title || 'Untitled Session'}</p>
                          <p className="text-[10px] text-cream-muted font-mono">{new Date(s.created_at).toLocaleString()}</p>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="text-[10px] text-cream-muted">{s.messages?.length || 0} msgs</span>
                          <span className={`text-cream-muted transition-transform ${expandedSession === s.id ? 'rotate-90' : ''}`}>›</span>
                        </div>
                      </button>
                      {expandedSession === s.id && (
                        <div className="px-4 py-3 space-y-3 bg-ink-surface max-h-80 overflow-y-auto">
                          {s.messages?.map((m) => (
                            <div key={m.id} className={`flex gap-2 ${m.sender === 'user' ? 'justify-end' : 'justify-start'}`}>
                              <div className={`max-w-[80%] px-3 py-2 rounded-xl text-xs ${
                                m.sender === 'user' ? 'bg-cyan-muted text-cream' : 'bg-ink-base border border-border-default text-cream-secondary'
                              }`}>
                                <p className="text-[9px] text-cream-muted mb-1 font-mono uppercase">{m.sender}</p>
                                {m.content}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function StatCard({ label, value }) {
  return (
    <div className="bg-ink-raised rounded-xl border border-border-subtle p-4">
      <p className="text-[10px] uppercase tracking-widest text-cream-muted mb-1">{label}</p>
      <p className="text-2xl font-display text-cream">{value ?? '—'}</p>
    </div>
  );
}

function SimpleModal({ title, value, setValue, placeholder, onConfirm, onClose, confirmLabel }) {
  return (
    <div className="fixed inset-0 bg-ink-deep/80 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-fade-in">
      <div className="bg-ink-base rounded-2xl border border-border-default shadow-elevated w-full max-w-sm p-6">
        <h3 className="font-display text-xl text-cream mb-4">{title}</h3>
        <input autoFocus type="text" value={value} onChange={(e) => setValue(e.target.value)}
          placeholder={placeholder}
          onKeyDown={(e) => { if (e.key === 'Enter') onConfirm(); if (e.key === 'Escape') onClose(); }}
          className="w-full px-4 py-3 rounded-lg bg-ink-deep border border-border-default text-cream text-sm placeholder:text-cream-muted focus:outline-none focus:border-cyan/40 mb-4 transition-colors" />
        <div className="flex gap-3">
          <button onClick={onClose} className="flex-1 py-2.5 rounded-lg border border-border-default text-cream-secondary text-sm hover:bg-ink-hover transition-all cursor-pointer">Cancel</button>
          <button onClick={onConfirm} disabled={!value.trim()} className="flex-1 py-2.5 rounded-lg gradient-cyan text-cream text-sm font-semibold disabled:opacity-40 cursor-pointer hover:brightness-110 transition-all">{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
