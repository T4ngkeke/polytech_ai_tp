/**
 * Teacher.jsx — Teacher workspace.
 * Layout: Left HierarchicalSidebar + Right context-sensitive panel.
 * Selected node determines right panel: Class (students/rules) | Lab (rules/analytics/audit).
 */

import { useState, useEffect, useCallback } from 'react';
import toast from 'react-hot-toast';
import api from '../lib/api';
import useAuthStore from '../store/authStore';
import HierarchicalSidebar from '../components/HierarchicalSidebar';
import DocumentManager from '../components/DocumentManager';
import TeacherAnalytics from '../components/TeacherAnalytics';
import QuotaRequests from '../components/QuotaRequests';
import RuleList from '../components/RuleList';
import CitationReport from '../components/CitationReport';
import Tour from '../components/Tour';
import { TOURS } from '../lib/tours';
import Icon from '../components/Icon';
import { EmptyState, Breadcrumb, SimpleModal } from './teacher/shared';
import StudentsPanel from './teacher/StudentsPanel';
import RulesPanel from './teacher/RulesPanel';
import { LabSettingsPanel, ClassSettingsPanel } from './teacher/SettingsPanels';
import TestDrivePanel from './teacher/TestDrivePanel';
import AuditPanel from './teacher/AuditPanel';

const TABS_CLASS = ['Settings', 'Rules', 'Analytics', 'Documents', 'Course use'];
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
  // The guided tour starts by itself until it has been finished or skipped once.
  const { tourSeen, markTourSeen } = useAuthStore();
  const [tourOpen, setTourOpen] = useState(false);
  useEffect(() => { if (!tourSeen) setTourOpen(true); }, [tourSeen]);

  // ── Right panel data ──
  const [students, setStudents] = useState([]);
  const [analytics, setAnalytics] = useState(null);
  const [auditSessions, setAuditSessions] = useState([]);
  const [expandedStudentId, setExpandedStudentId] = useState(null);
  const [expandedSession, setExpandedSession] = useState(null);

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
      if (activeTab === 2) {
        loadAnalytics();
      }
    } else {
      if (activeTab === 2) {
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
        // The open lab too: its Settings button must offer the other action next.
        setSelectedLab((prev) => (prev?.id === lab.id ? { ...prev, ...updated } : prev));
        setSelectedClass((prev) => (prev?.labs ? { ...prev, labs: prev.labs.map((l) => (l.id === lab.id ? updated : l)) } : prev));
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

  const handleKickStudent = async (studentId) => {
    if (!selectedClass || !confirm('Remove this student from class?')) return;
    try {
      await api.delete(`/api/teacher/classes/${selectedClass.id}/students/${studentId}`);
      setStudents((prev) => prev.filter((s) => s.id !== studentId));
      toast.success('Student removed');
    } catch (err) { toast.error(err.message); }
  };

  const handleOpenStudentRule = (student) => {
    setStudentRuleModal({ studentId: student.id, username: student.username });
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
          onHelp={() => setTourOpen(true)}
        />
      </div>

      {/* RIGHT: Panel */}
      <div className="flex-1 flex flex-col min-w-0">
        <div className="md:hidden shrink-0 h-12 px-2 flex items-center gap-2 border-b border-border-subtle bg-ink-base">
          <button type="button" onClick={() => setNavOpen(true)} title="Classes and labs" aria-label="Classes and labs"
            className="p-2 rounded-lg text-cream-secondary hover:text-cream hover:bg-ink-hover cursor-pointer">
            <Icon name="menu" className="w-5 h-5" />
          </button>
          {selectedClass
            ? <Breadcrumb cls={selectedClass} lab={selectedLab} onClass={() => handleSelectClass(selectedClass)}
                className="min-w-0 truncate text-sm font-medium text-cream" />
            : <span className="min-w-0 truncate text-sm font-medium text-cream">Teacher</span>}
        </div>
        <QuotaRequests onReset={activeTab === tabs.indexOf('Analytics') ? loadAnalytics : undefined} />
        {!selectedClass && !selectedLab ? (
          <EmptyState />
        ) : (
          <>
            {/* Panel header */}
            <div className="shrink-0 px-4 pt-2 md:px-8 md:pt-6 pb-0 border-b border-border-subtle bg-ink-base/60">
              <Breadcrumb cls={selectedClass} lab={selectedLab} onClass={() => handleSelectClass(selectedClass)}
                className="hidden md:block mb-1 text-[11px] text-cream-muted uppercase tracking-widest font-mono" />
              {/* Phones: the tabs wrap onto a second line, so none is hidden off screen. */}
              <div data-tour="tabs" className="flex flex-wrap gap-x-5 md:flex-nowrap md:gap-6 md:overflow-x-auto no-scrollbar">
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
              {level === 'class' && activeTab === 1 && <RulesPanel level="class" targetId={selectedClass.id} classId={selectedClass.id} context={context} />}
              {level === 'class' && activeTab === 2 && (
                <TeacherAnalytics data={analytics} classId={selectedClass.id} onReload={loadAnalytics}
                  onOpenConversation={openConversation} />
              )}
              {level === 'class' && activeTab === 3 && <DocumentManager classId={selectedClass.id}
                examMode={!!selectedClass.exam_mode} onOpenClassSettings={() => setActiveTab(0)} />}
              {level === 'class' && activeTab === 4 && (
                <CitationReport classId={selectedClass.id} name={selectedClass.name}
                  onOpenConversation={(userId, sessionId, labId) => openConversation(
                    { lab_id: labId, user_id: userId, session_id: sessionId })} />
              )}

              {level === 'lab' && activeTab === 0 && <LabSettingsPanel lab={selectedLab} onToggle={() => handleLabAction('toggle', selectedLab)} />}
              {level === 'lab' && activeTab === 1 && <RulesPanel level="lab" targetId={selectedLab.id} classId={selectedClass.id} context={context} />}
              {level === 'lab' && activeTab === 2 && (
                <TeacherAnalytics data={analytics} classId={selectedClass.id} onReload={loadAnalytics}
                  onOpenConversation={openConversation}
                  onOpenStudent={(userId) => openConversation({ user_id: userId })} />
              )}
              {level === 'lab' && activeTab === 3 && <AuditPanel lab={selectedLab} sessions={auditSessions} students={students} expandedStudentId={expandedStudentId} setExpandedStudentId={setExpandedStudentId} expandedSession={expandedSession} setExpandedSession={setExpandedSession} />}
              {level === 'lab' && activeTab === 4 && <StudentsPanel students={students} onKick={handleKickStudent} onSetRule={handleOpenStudentRule} />}
              {level === 'lab' && activeTab === 5 && <DocumentManager labId={selectedLab.id}
                examMode={!!selectedClass?.exam_mode} onOpenClassSettings={() => handleSelectClass(selectedClass)}
                onOpenClassDocuments={() => { handleSelectClass(selectedClass); setActiveTab(3); }} />}
              {level === 'lab' && activeTab === 6 && <TestDrivePanel labId={selectedLab.id} labName={selectedLab.name} classId={selectedClass?.id} examMode={!!selectedClass?.exam_mode} />}
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
      {tourOpen && (
        <Tour steps={TOURS.teacher} onNav={setNavOpen}
          onClose={() => { setTourOpen(false); if (!tourSeen) markTourSeen(); }} />
      )}
      {studentRuleModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4"
          onClick={(e) => { if (e.target === e.currentTarget) setStudentRuleModal(null); }}>
          <div className="bg-ink-surface border border-border-default rounded-2xl w-full max-w-lg max-h-[90dvh] overflow-y-auto p-6 shadow-elevated animate-scale-in">
            <div className="mb-4 flex items-start justify-between gap-3">
              <div>
                <h3 className="text-lg font-semibold text-cream mb-1">Rules for one student</h3>
                <p className="text-xs text-cream-muted">Only for <span className="text-cyan font-medium">{studentRuleModal.username}</span>, in {selectedClass?.name}. They come after the class and lab rules.</p>
              </div>
              <button onClick={() => setStudentRuleModal(null)} aria-label="Close"
                className="rounded-lg p-1.5 text-cream-muted hover:bg-ink-hover hover:text-cream cursor-pointer">
                <Icon name="close" className="h-4 w-4" />
              </button>
            </div>
            <RuleList level="student" targetId={studentRuleModal.studentId} classId={selectedClass.id} />
          </div>
        </div>
      )}
    </div>
  );
}
