/**
 * Teacher.jsx — Teacher workspace v7.2.
 * Layout: Left HierarchicalSidebar + Right context-sensitive panel.
 * Selected node determines right panel: Class (students/rules) | Lab (rules/analytics/audit).
 */

import { useState, useEffect, useCallback, useRef, lazy, Suspense } from 'react';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import toast from 'react-hot-toast';
import api from '../lib/api';
import { createMessageId, createRequestId } from '../lib/messageId';
import { interpretStreamEvent } from '../lib/streamEvents';
import { applyToMessage } from '../lib/streamMessage';
import { MessageBubble } from '../components/ChatMessage';
import { PdfPreviewModal } from '../components/PdfPreview';
import useAuthStore from '../store/authStore';
import HierarchicalSidebar from '../components/HierarchicalSidebar';
import DocumentManager from '../components/DocumentManager';
import TeacherAnalytics from '../components/TeacherAnalytics';
import TeachingReport from '../components/TeachingReport';
import SkillPresetManager from '../components/SkillPresetManager';
import CitationReport from '../components/CitationReport';
import ModeSwitch from '../components/ModeSwitch';
import { DEFAULT_MODE, modeLabel, offersResearch } from '../lib/answerModes';
import { useChatScroll } from '../lib/chatScroll';
import Icon from '../components/Icon';

// Heavy (markdown + KaTeX + highlight) — lazy so the teacher bundle only pulls
// it in when a test-drive is actually opened.
const MessageContent = lazy(() => import('../components/MessageContent'));

const TABS_CLASS = ['Settings', 'Rules', 'Analytics', 'Documents'];
// New tabs appended last so existing tab indices stay stable.
const TABS_LAB = ['Settings', 'Rules', 'Analytics', 'Audit', 'Students', 'Documents', 'Test drive'];

export default function Teacher() {
  // ── Tree state ──
  const [classes, setClasses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedClass, setSelectedClass] = useState(null);
  const [selectedLab, setSelectedLab] = useState(null);
  const [activeTab, setActiveTab] = useState(0);
  // Phones: the class tree is a drawer opened from the top bar.
  const [navOpen, setNavOpen] = useState(true);

  // ── Right panel data ──
  const [students, setStudents] = useState([]);
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
    setNavOpen(false);
  }, [classes]);

  // A class changed on the server (exam mode): keep its labs, take the new fields.
  const handleClassChanged = useCallback((updated) => {
    setClasses((prev) => prev.map((c) => (c.id === updated.id ? { ...c, ...updated } : c)));
    setSelectedClass((prev) => (prev && prev.id === updated.id ? { ...prev, ...updated } : prev));
  }, []);

  // ── When class node clicked (no lab): set selectedClass, clear lab ──
  const handleSelectClass = useCallback((cls) => {
    setSelectedClass(cls);
    setSelectedLab(null);
    setActiveTab(0);
    setStudents([]);
    setAnalytics(null);
  }, []);

  // Analytics of the class, or of the selected lab only.
  const loadAnalytics = useCallback(() => {
    if (!selectedClass) return Promise.resolve();
    const lab = selectedLab ? `?lab_id=${selectedLab.id}` : '';
    return api.get(`/api/teacher/analytics/classes/${selectedClass.id}/details${lab}`)
      .then(setAnalytics).catch(() => setAnalytics(null));
  }, [selectedClass, selectedLab]);

  // From Analytics to the conversation in Audit (Audit is a lab tab).
  const openConversation = useCallback(({ lab_id: labId, user_id: userId, session_id: sessionId }) => {
    if (labId && labId !== selectedLab?.id) handleSelectLab(labId, selectedClass.id);
    setActiveTab(3);
    setExpandedStudentId(userId || null);
    setExpandedSession(sessionId || null);
  }, [selectedLab, selectedClass, handleSelectLab]);

  // ── Load tab data on tab change ──
  useEffect(() => {
    if (!selectedClass && !selectedLab) return;
    const level = selectedLab ? 'lab' : 'class';

    if (level === 'class') {
      if (activeTab === 1) {
        api.get(`/api/teacher/rules?level=class&target_id=${selectedClass.id}`)
          .then((rules) => { const r = rules[0] || null; setRuleText(r?.rules_text || ''); setRuleActive(r?.is_active ?? true); })
          .catch(() => { setRuleText(''); });
      } else if (activeTab === 2) {
        loadAnalytics();
      }
    } else {
      if (activeTab === 1) {
        Promise.all([
          api.get(`/api/teacher/rules?level=lab&target_id=${selectedLab.id}`),
          api.get(`/api/teacher/rules?level=class&target_id=${selectedClass.id}`)
        ])
          .then(([labRules, classRules]) => {
            const r = labRules[0] || null; 
            setRuleText(r?.rules_text || ''); setRuleActive(r?.is_active ?? true);
            setInheritedClassRule(classRules[0]?.rules_text || '');
          })
          .catch(() => { setRuleText(''); setInheritedClassRule(''); });
      } else if (activeTab === 2) {
        loadAnalytics();
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
  }, [activeTab, selectedClass, selectedLab, loadAnalytics]);

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
      await api.put('/api/teacher/rules', { level, target_id: targetId, rules_text: ruleText, is_active: ruleActive });
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
    <div className="flex h-dvh bg-ink-deep overflow-hidden">
      {/* LEFT: Tree (a drawer on phones) */}
      {navOpen && (
        <div className="fixed inset-0 z-30 bg-black/40 md:hidden" onClick={() => setNavOpen(false)} />
      )}
      <div className={`${navOpen ? 'block' : 'hidden'} fixed inset-y-0 left-0 z-40 w-[85vw] max-w-xs shadow-elevated
        md:static md:z-auto md:block md:w-64 md:max-w-none md:shadow-none shrink-0`}>
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
        <div className="md:hidden shrink-0 h-12 px-2 flex items-center gap-2 border-b border-border-subtle bg-ink-base">
          <button type="button" onClick={() => setNavOpen(true)} title="Classes and labs" aria-label="Classes and labs"
            className="p-2 rounded-lg text-cream-secondary hover:text-cream hover:bg-ink-hover cursor-pointer">
            <MenuIcon className="w-5 h-5" />
          </button>
          {selectedClass
            ? <Breadcrumb cls={selectedClass} lab={selectedLab} onClass={() => handleSelectClass(selectedClass)}
                className="min-w-0 truncate text-sm font-medium text-cream" />
            : <span className="min-w-0 truncate text-sm font-medium text-cream">Teacher</span>}
        </div>
        {!selectedClass && !selectedLab ? (
          <EmptyState />
        ) : (
          <>
            {/* Panel header */}
            <div className="shrink-0 px-4 pt-2 md:px-8 md:pt-6 pb-0 border-b border-border-subtle bg-ink-base/60">
              <Breadcrumb cls={selectedClass} lab={selectedLab} onClass={() => handleSelectClass(selectedClass)}
                className="hidden md:block mb-1 text-[11px] text-cream-muted uppercase tracking-widest font-mono" />
              <div className="flex gap-5 md:gap-6 overflow-x-auto no-scrollbar">
                {tabs.map((tab, i) => (
                  <button
                    key={tab}
                    onClick={(e) => { setActiveTab(i); e.currentTarget.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }}
                    className={`shrink-0 whitespace-nowrap pb-3 text-sm font-medium border-b-2 transition-all cursor-pointer ${
                      activeTab === i ? 'border-cyan text-cyan' : 'border-transparent text-cream-secondary hover:text-cream'
                    }`}
                  >
                    {tab}
                  </button>
                ))}
              </div>
            </div>

            {/* Panel content */}
            <div className="flex-1 p-4 md:p-8 overflow-y-auto">
              {level === 'class' && activeTab === 0 && <ClassSettingsPanel cls={selectedClass} onChange={handleClassChanged} />}
              {level === 'class' && activeTab === 1 && <RulesPanel ruleText={ruleText} setRuleText={setRuleText} ruleActive={ruleActive} setRuleActive={setRuleActive} onSave={handleSaveRule} context={context} inheritedRule="" showPresets />}
              {level === 'class' && activeTab === 2 && (
                <TeacherAnalytics data={analytics} classId={selectedClass.id} onReload={loadAnalytics}
                  onOpenConversation={openConversation} />
              )}
              {level === 'class' && activeTab === 3 && <DocumentManager classId={selectedClass.id} />}

              {level === 'lab' && activeTab === 0 && <LabSettingsPanel lab={selectedLab} onToggle={() => handleLabAction('toggle', selectedLab)} />}
              {level === 'lab' && activeTab === 1 && <RulesPanel ruleText={ruleText} setRuleText={setRuleText} ruleActive={ruleActive} setRuleActive={setRuleActive} onSave={handleSaveRule} context={context} inheritedRule={inheritedClassRule} />}
              {level === 'lab' && activeTab === 2 && (
                <TeacherAnalytics data={analytics} classId={selectedClass.id} onReload={loadAnalytics}
                  onOpenConversation={openConversation}
                  onOpenStudent={(userId) => openConversation({ user_id: userId })} />
              )}
              {level === 'lab' && activeTab === 3 && <AuditPanel lab={selectedLab} sessions={auditSessions} students={students} expandedStudentId={expandedStudentId} setExpandedStudentId={setExpandedStudentId} expandedSession={expandedSession} setExpandedSession={setExpandedSession} />}
              {level === 'lab' && activeTab === 4 && <StudentsPanel students={students} onKick={handleKickStudent} onSetRule={handleOpenStudentRule} />}
              {level === 'lab' && activeTab === 5 && <DocumentManager labId={selectedLab.id} />}
              {level === 'lab' && activeTab === 6 && <TestDrivePanel labId={selectedLab.id} labName={selectedLab.name} examMode={!!selectedClass?.exam_mode} />}
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
              <button onClick={handleSaveStudentRule} className="px-4 py-2 rounded-lg gradient-cyan text-on-accent text-sm font-semibold hover:brightness-110 transition-colors">Save</button>
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
          <svg className="w-8 h-8 text-on-accent" fill="none" stroke="currentColor" strokeWidth="1.5" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 10h16M4 14h8M4 18h8" />
          </svg>
        </div>
        <h3 className="font-display text-xl text-cream mb-2">Select a node</h3>
        <p className="text-sm text-cream-secondary">Choose a class or lab to manage it.</p>
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

// "Class › Lab" above the tabs. The class name opens the class; the lab is the
// page already shown.
function Breadcrumb({ cls, lab, onClass, className }) {
  if (!cls) return null;
  return (
    <nav aria-label="Breadcrumb" className={className}>
      {lab ? (
        <button type="button" onClick={onClass} title={`Open ${cls.name}`}
          className="-my-1 py-1 [text-transform:inherit] cursor-pointer hover:text-cyan hover:underline underline-offset-2">
          {cls.name}
        </button>
      ) : <span aria-current="page">{cls.name}</span>}
      {lab && (
        <>
          <span aria-hidden="true" className="mx-1.5">›</span>
          <span aria-current="page" className="text-cream-secondary">{lab.name}</span>
        </>
      )}
    </nav>
  );
}

function RulesPanel({ ruleText, setRuleText, ruleActive, setRuleActive, onSave, context, inheritedRule, showPresets }) {
  return (
    <div className="max-w-2xl space-y-4">
      <div>
        <h3 className="text-sm font-semibold text-cream mb-1">Prompt Rules</h3>
        <p className="text-xs text-cream-muted mb-4">Target: <span className="text-cyan font-mono">{context}</span></p>
      </div>

      {showPresets && (
        <SkillPresetManager currentText={ruleText} onUse={(content) => setRuleText(content)} />
      )}

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
          className="px-5 py-2 rounded-lg gradient-cyan text-on-accent text-sm font-semibold hover:brightness-110 transition-all cursor-pointer disabled:opacity-40">
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
            lab.is_active ? 'bg-danger-muted text-danger hover:bg-danger/20' : 'bg-success-muted text-success hover:bg-success/25'
          }`}>
          <span className="inline-flex items-center gap-1.5">
            <Icon name={lab.is_active ? 'lock' : 'unlock'} className="w-3.5 h-3.5" />
            {lab.is_active ? 'Lock' : 'Unlock'}
          </span>
        </button>
      </div>
    </div>
  );
}

// Teacher test-drive — opens an is_test session on the lab and chats through
// the real agent as a preview. The server excludes these writes from analytics
// and LearnerProfile, and the token cost is charged to the teacher, not a
// student.
function ClassSettingsPanel({ cls, onChange }) {
  const [saving, setSaving] = useState(false);
  const toggle = async () => {
    setSaving(true);
    try {
      onChange(await api.put(`/api/teacher/classes/${cls.id}/exam-mode`, { exam_mode: !cls.exam_mode }));
      toast.success(cls.exam_mode ? 'Exam mode off' : 'Exam mode on');
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="max-w-2xl p-5 rounded-xl bg-ink-raised border border-border-subtle">
      <h3 className="text-sm font-semibold text-cream mb-4">Class Settings</h3>
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-cream">Exam mode</p>
          <p className="mt-1 text-xs text-cream-muted">
            {cls.exam_mode
              ? 'On: in every lab of this class, students get Direct answers only. The course files are not searched.'
              : 'Off: students choose Direct, Search or Research. Turn it on during an exam to stop course search in every lab.'}
          </p>
        </div>
        <button type="button" role="switch" aria-checked={!!cls.exam_mode} aria-label="Exam mode"
          onClick={toggle} disabled={saving}
          className={`shrink-0 w-9 h-5 rounded-full transition-colors relative cursor-pointer disabled:opacity-50 ${cls.exam_mode ? 'bg-gold' : 'bg-ink-surface border border-border-default'}`}>
          <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${cls.exam_mode ? 'left-[18px]' : 'left-0.5'}`} />
        </button>
      </div>
    </div>
  );
}

function TestDrivePanel({ labId, labName, examMode = false }) {
  // The same agent, stream and message bubbles as the student chat: research
  // phases, the answer draft, a question back with clickable options, and
  // cited pages that open in the source preview.
  const { token } = useAuthStore();
  const [sessionId, setSessionId] = useState(null);
  const [starting, setStarting] = useState(false);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [phase, setPhase] = useState(null);
  const [preview, setPreview] = useState(null);
  const [mode, setMode] = useState(DEFAULT_MODE);
  const abortRef = useRef(null);
  const scrollRef = useRef(null);
  const { onScroll, update: updateScroll, follow } = useChatScroll(scrollRef);

  // Reset (and abort any in-flight stream) when the selected lab changes.
  useEffect(() => {
    setSessionId(null);
    setMessages([]);
    setInput('');
    setIsStreaming(false);
    setPhase(null);
    setPreview(null);
    return () => abortRef.current?.abort();
  }, [labId]);

  useEffect(() => {
    updateScroll();
  }, [messages, updateScroll]);

  const start = useCallback(async () => {
    setStarting(true);
    try {
      const { session_id } = await api.post(`/api/teacher/labs/${labId}/test-session`);
      setSessionId(session_id);
      setMessages([]);
    } catch {
      toast.error('Could not start test drive');
    } finally {
      setStarting(false);
    }
  }, [labId]);

  // picked: an option clicked under a question back, or a question sent again
  // (modeOverride: Research again after an uncited Search); otherwise the input box.
  const send = useCallback(async (picked, modeOverride) => {
    const text = (typeof picked === 'string' ? picked : input).trim();
    if (!text || isStreaming || !sessionId) return;

    const assistantMsgId = createMessageId();
    setMessages((prev) => [
      ...prev,
      { id: createMessageId(), sender: 'user', content: text },
      { id: assistantMsgId, sender: 'llm', content: '' },
    ]);
    follow('pin');
    if (typeof picked !== 'string') setInput('');
    setIsStreaming(true);

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      // Same agent endpoint the students hit — the server bypasses the
      // membership check for an owning teacher's is_test session.
      await fetchEventSource('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ session_id: sessionId, message: text, client_request_id: createRequestId(),
          mode: examMode ? 'direct' : (typeof modeOverride === 'string' ? modeOverride : mode) }),
        signal: ctrl.signal,
        openWhenHidden: true,
        onmessage(ev) {
          const action = interpretStreamEvent(ev);
          switch (action.type) {
            case 'status':
              setPhase(action.phase);
              if (action.mode) {
                setMessages((prev) => prev.map((m) => (m.id === assistantMsgId ? { ...m, answer_mode: action.mode } : m)));
              }
              return;
            case 'done':
              setPhase(null);
              setIsStreaming(false);
              return;
            case 'error':
              setIsStreaming(false);
              setMessages((prev) => prev.map((m) => (m.id === assistantMsgId ? { ...m, draft: '' } : m)));
              toast.error(action.detail);
              return;
            default:
              setMessages((prev) => prev.map((m) => (m.id === assistantMsgId ? applyToMessage(m, action) : m)));
          }
        },
        onerror(err) { throw err; },
      });
    } catch {
      if (!ctrl.signal.aborted) toast.error('Stream interrupted');
    } finally {
      // An unchecked draft never stays on screen once the stream ends.
      setMessages((prev) => prev.map((m) => (m.id === assistantMsgId && m.draft ? { ...m, draft: '' } : m)));
      setPhase(null);
      setIsStreaming(false);
    }
  }, [input, isStreaming, sessionId, token, mode, examMode, follow]);

  if (!sessionId) {
    return (
      <div className="p-5 rounded-xl bg-ink-raised border border-border-subtle">
        <p className="text-sm font-semibold text-cream">Test drive</p>
        <p className="mt-1 text-xs text-cream-muted">
          Chat through the real tutor as a preview of <span className="text-cream-secondary">{labName}</span>.
          These messages are excluded from analytics and student profiles.
        </p>
        <button
          onClick={start}
          disabled={starting}
          className="mt-4 px-4 py-2 rounded-lg gradient-cyan text-on-accent text-sm font-medium disabled:opacity-50"
        >
          {starting ? 'Starting…' : 'Start test drive'}
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full min-h-[24rem] rounded-xl bg-ink-raised border border-border-subtle overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border-subtle">
        <span className="text-sm font-semibold text-cream">Test drive · {labName}</span>
        <button onClick={start} className="text-xs text-cream-muted hover:text-cyan">Restart</button>
      </div>
      <div ref={scrollRef} onScroll={onScroll} className="relative flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <p className="text-sm text-cream-muted">Ask as if you were a student — e.g. “comment faire l’exercice 3 ?”.</p>
        )}
        {messages.map((m, i) => {
          const last = i === messages.length - 1;
          return (
            <div key={m.id} data-latest-answer={last && m.sender === 'llm' ? '' : undefined}>
            <MessageBubble message={m} sessionId={sessionId} onOpenSource={setPreview}
              streaming={isStreaming && m.sender === 'llm' && last}
              phase={isStreaming && m.sender === 'llm' && last ? phase : null}
              canRate={false}
              onPick={!isStreaming && last && m.clarification ? send : null}
              onResearch={!isStreaming && !examMode && last && offersResearch(m) ? () => {
                const question = messages.slice(0, i).reverse().find((x) => x.sender === 'user');
                if (question) send(question.content, 'research');
              } : null} />
            </div>
          );
        })}
      </div>
      <div className="px-3 pt-3 border-t border-border-subtle">
        <ModeSwitch mode={mode} onChange={setMode} examMode={examMode} disabled={isStreaming} />
      </div>
      <div className="flex gap-2 p-3">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
          placeholder="Message the tutor…"
          className="flex-1 px-3 py-2 rounded-lg bg-ink-deep border border-border-subtle text-sm text-cream placeholder:text-cream-muted focus:outline-none focus:border-cyan"
        />
        <button
          onClick={send}
          disabled={isStreaming || !input.trim()}
          className="px-4 py-2 rounded-lg gradient-cyan text-on-accent text-sm font-medium disabled:opacity-50"
        >
          Send
        </button>
      </div>
      {preview && <PdfPreviewModal source={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}

function AuditPanel({ lab, sessions, students, expandedStudentId, setExpandedStudentId, expandedSession, setExpandedSession }) {
  // Three views of the same conversations: per student, which course material
  // the answers cite ("open conversation" leads back to a student), and a
  // report the model writes on the lab when the teacher asks.
  const [view, setView] = useState('students');
  const openConversation = useCallback((studentId, sessionId) => {
    setView('students');
    setExpandedStudentId(studentId);
    setExpandedSession(sessionId);
  }, [setExpandedStudentId, setExpandedSession]);
  const views = (
    <div className="flex gap-1 mb-4" role="tablist" aria-label="Audit view">
      {[['students', 'By student'], ['course', 'Course use'], ['report', 'AI report']].map(([key, label]) => (
        <button key={key} type="button" role="tab" aria-selected={view === key} onClick={() => setView(key)}
          className={`px-3 py-1.5 rounded-lg text-xs cursor-pointer transition-colors ${
            view === key ? 'bg-cyan-muted text-cream border border-cyan/20' : 'text-cream-muted hover:bg-ink-hover'}`}>
          {label}
        </button>
      ))}
    </div>
  );
  if (view === 'report') {
    return (
      <div className="max-w-3xl">
        <h3 className="text-sm font-semibold text-cream mb-4">Chat Audit</h3>
        {views}
        <TeachingReport labId={lab.id} />
      </div>
    );
  }
  if (view === 'course') {
    return (
      <div>
        <h3 className="text-sm font-semibold text-cream mb-4">Chat Audit</h3>
        {views}
        <CitationReport labId={lab.id} labName={lab.name} onOpenConversation={openConversation} />
      </div>
    );
  }
  return (
    <div className="space-y-3 max-w-3xl">
      <h3 className="text-sm font-semibold text-cream mb-4">Chat Audit</h3>
      {views}
      {(!students || students.length === 0) && <p className="text-cream-muted text-sm">No students in this class yet.</p>}
      {(students || []).map((student) => {
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
                <p className="text-xs text-cream-muted font-mono">{studentSessions.length} session{studentSessions.length === 1 ? '' : 's'}</p>
              </div>
              <span className={`text-cream-muted transition-transform ${isStudentExpanded ? 'rotate-90' : ''}`}>›</span>
            </button>
            
            {isStudentExpanded && (
              <div className="border-t border-border-subtle bg-ink-base">
                {studentSessions.length > 0 && (
                  <div className="border-b border-border-subtle p-3">
                    <TeachingReport labId={lab.id} studentId={student.id} studentName={student.username} />
                  </div>
                )}
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
                          <p className="text-xs font-medium text-cream">
                            <span className={s.is_deleted ? 'line-through opacity-70' : ''}>{s.title || 'New conversation'}</span>
                            {s.is_deleted && (
                              <span className="ml-2 align-middle rounded bg-danger-muted px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-danger">
                                deleted by student
                              </span>
                            )}
                          </p>
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
                                <p className="text-[9px] text-cream-muted mb-1 font-mono uppercase">
                                  {m.sender}{m.sender !== 'user' && modeLabel(m.answer_mode) ? ` · ${modeLabel(m.answer_mode)}` : ''}
                                </p>
                                {/* Answers are Markdown (tables, code, formulas), as the student saw them. */}
                                {m.sender === 'user'
                                  ? <span className="whitespace-pre-wrap">{m.content}</span>
                                  : <Suspense fallback={<span className="whitespace-pre-wrap">{m.content}</span>}>
                                      <MessageContent content={m.content} />
                                    </Suspense>}
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
          <button onClick={onConfirm} disabled={!value.trim()} className="flex-1 py-2.5 rounded-lg gradient-cyan text-on-accent text-sm font-semibold disabled:opacity-40 cursor-pointer hover:brightness-110 transition-all">{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}

function MenuIcon({ className }) {
  return <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M4 6h16M4 12h16M4 18h16" /></svg>;
}
