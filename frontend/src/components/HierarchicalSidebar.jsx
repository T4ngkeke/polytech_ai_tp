/**
 * HierarchicalSidebar.jsx — Role-aware Class→Lab tree navigation for Edu-LLM v7.2.
 *
 * Props:
 *   role: 'student' | 'teacher' | 'admin'
 *   classes: ClassNode[]      — pre-fetched class list
 *   selectedLabId: string     — currently active lab
 *   onSelectLab(labId, classId) — called when a lab is clicked
 *   onJoinClass()             — (student) opens the join modal
 *   onCreateClass()           — (teacher) triggers class creation
 *   onClassAction(action, cls) — (teacher/admin) rename/delete/reset-code
 *   onLabAction(action, lab)  — (teacher/admin) toggle/rename/delete
 *   loading: bool
 *
 * ClassNode shape: { id, name, invite_code, labs: LabNode[] }
 * LabNode shape:   { id, name, is_active, is_deleted }
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import useAuthStore from '../store/authStore';

export default function HierarchicalSidebar({
  role = 'student',
  classes = [],
  selectedLabId = null,
  onSelectClass,
  onSelectLab,
  onJoinClass,
  onCreateClass,
  onClassAction,
  onLabAction,
  loading = false,
}) {
  const { username, userRole, logout } = useAuthStore();
  const navigate = useNavigate();

  const [expandedClassIds, setExpandedClassIds] = useState(() => {
    // Auto-expand all classes by default
    return new Set(classes.map((c) => c.id));
  });

  const handleSignOut = () => {
    logout();
    navigate('/login', { replace: true });
  };

  const toggleClass = (classId) => {
    setExpandedClassIds((prev) => {
      const next = new Set(prev);
      if (next.has(classId)) next.delete(classId);
      else next.add(classId);
      return next;
    });
  };

  return (
    <div className="flex flex-col h-full bg-ink-base border-r border-border-subtle select-none">
      {/* Header */}
      <div className="px-4 pt-5 pb-3 border-b border-border-subtle shrink-0">
        <h2 className="text-xs font-semibold uppercase tracking-widest text-cream-muted mb-1">
          {role === 'student' ? 'My Classes' : role === 'teacher' ? 'My Classes' : 'All Classes'}
        </h2>
        {role === 'student' && (
          <button
            id="join-class-btn"
            onClick={onJoinClass}
            className="mt-2 w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg border border-border-default text-xs font-medium text-cream-secondary hover:text-cyan hover:border-cyan/30 hover:bg-cyan-muted transition-all cursor-pointer"
          >
            <PlusIcon className="w-3.5 h-3.5" /> Join a Class
          </button>
        )}
        {(role === 'teacher' || role === 'admin') && (
          <button
            id="create-class-btn"
            onClick={onCreateClass}
            className="mt-2 w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg border border-border-default text-xs font-medium text-cream-secondary hover:text-cyan hover:border-cyan/30 hover:bg-cyan-muted transition-all cursor-pointer"
          >
            <PlusIcon className="w-3.5 h-3.5" /> New Class
          </button>
        )}
      </div>

      {/* Tree */}
      <div className="flex-1 overflow-y-auto px-2 py-2">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <div className="w-4 h-4 rounded-full border-2 border-cyan border-t-transparent animate-[spin_0.8s_linear_infinite]" />
          </div>
        ) : classes.length === 0 ? (
          <div className="text-center py-10 px-3">
            <p className="text-xs text-cream-muted">
              {role === 'student' ? 'Join a class to get started.' : 'No classes yet.'}
            </p>
          </div>
        ) : (
          classes.map((cls) => (
            <ClassNode
              key={cls.id}
              cls={cls}
              role={role}
              isExpanded={expandedClassIds.has(cls.id)}
              onToggle={() => toggleClass(cls.id)}
              selectedLabId={selectedLabId}
              onSelectClass={onSelectClass}
              onSelectLab={onSelectLab}
              onClassAction={onClassAction}
              onLabAction={onLabAction}
            />
          ))
        )}
      </div>

      {/* Footer (User Profile & Sign Out) */}
      <div className="shrink-0 p-4 border-t border-border-subtle bg-ink-base">
        <div className="flex items-center justify-between">
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-cream truncate">{username || 'User'}</p>
            <p className="text-[10px] uppercase tracking-wider text-cyan">{userRole}</p>
          </div>
          <div className="shrink-0 ml-3 flex items-center gap-2">
            <button
              onClick={() => navigate('/account')}
              title="Account"
              className="p-2 rounded-lg bg-ink-surface border border-border-default text-cream-secondary hover:text-cyan hover:border-cyan/30 transition-all cursor-pointer"
            >
              <SettingsIcon className="w-4 h-4" />
            </button>
            <button
              onClick={handleSignOut}
              title="Sign Out"
              className="p-2 rounded-lg bg-ink-surface border border-border-default text-cream-secondary hover:text-danger hover:border-danger/30 hover:bg-danger-deep transition-all cursor-pointer"
            >
              <LogOutIcon className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════
   CLASS NODE
   ═══════════════════════════════════════════ */

function ClassNode({
  cls,
  role,
  isExpanded,
  onToggle,
  selectedLabId,
  onSelectClass,
  onSelectLab,
  onClassAction,
  onLabAction,
}) {
  const [showMenu, setShowMenu] = useState(false);
  const hasLabs = cls.labs && cls.labs.length > 0;

  return (
    <div className="mb-0.5">
      {/* Class row */}
      <div
        onClick={() => { onToggle(); onSelectClass?.(cls); }}
        className="group flex items-center gap-1 px-2 py-2 rounded-lg hover:bg-ink-hover transition-colors cursor-pointer text-left"
      >
        {/* Expand toggle */}
        <span className="shrink-0 w-4 h-4 flex items-center justify-center text-cream-muted group-hover:text-cream transition-colors">
          <ChevronIcon className={`w-3 h-3 transition-transform ${isExpanded ? 'rotate-90' : ''}`} />
        </span>

        {/* Class name */}
        <span className="flex-1 min-w-0 text-left text-xs font-semibold text-cream-secondary group-hover:text-cream transition-colors truncate">
          {cls.name}
        </span>

        {/* Invite code badge (student/teacher) */}
        {role !== 'admin' && cls.invite_code && (
          <code className="shrink-0 text-[9px] font-mono text-gold bg-gold-muted px-1.5 py-0.5 rounded opacity-0 group-hover:opacity-100 transition-opacity">
            {cls.invite_code}
          </code>
        )}

        {/* Actions menu (teacher/admin) */}
        {(role === 'teacher' || role === 'admin') && (
          <div className="relative shrink-0">
            <button
              onClick={(e) => { e.stopPropagation(); setShowMenu(!showMenu); }}
              className="opacity-0 group-hover:opacity-100 w-5 h-5 flex items-center justify-center text-cream-muted hover:text-cream rounded transition-all cursor-pointer"
            >
              <EllipsisIcon className="w-3.5 h-3.5" />
            </button>
            {showMenu && (
              <ClassMenu
                role={role}
                cls={cls}
                onAction={(action) => { setShowMenu(false); onClassAction?.(action, cls); }}
                onClose={() => setShowMenu(false)}
              />
            )}
          </div>
        )}
      </div>

      {/* Labs list */}
      {isExpanded && (
        <div className="ml-5 mt-0.5 space-y-0.5 mb-1 animate-fade-in">
          {!hasLabs ? (
            <p className="text-[11px] text-cream-muted px-2 py-1 italic">No labs yet</p>
          ) : (
            cls.labs.map((lab) => (
              <LabNode
                key={lab.id}
                lab={lab}
                classId={cls.id}
                role={role}
                isSelected={selectedLabId === lab.id}
                onSelect={() => onSelectLab?.(lab.id, cls.id)}
                onLabAction={onLabAction}
              />
            ))
          )}
          {/* Teacher/admin: add lab button */}
          {(role === 'teacher' || role === 'admin') && (
            <button
              onClick={() => onLabAction?.('create', { class_id: cls.id })}
              className="w-full text-left text-[11px] text-cream-muted hover:text-cyan px-2 py-1 rounded transition-colors cursor-pointer"
            >
              + Add Lab
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════
   LAB NODE
   ═══════════════════════════════════════════ */

function LabNode({ lab, classId, role, isSelected, onSelect, onLabAction }) {
  const [showMenu, setShowMenu] = useState(false);
  const isLocked = lab.is_deleted || !lab.is_active;

  return (
    <div className="group relative flex items-center">
      <button
        onClick={!isLocked ? onSelect : undefined}
        disabled={isLocked}
        className={`flex-1 min-w-0 flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs transition-all cursor-pointer text-left
          ${isSelected
            ? 'bg-cyan-muted text-cyan font-medium'
            : isLocked
              ? 'text-cream-muted opacity-50 cursor-not-allowed'
              : 'text-cream-secondary hover:text-cream hover:bg-ink-hover'
          }`}
      >
        {/* Status dot */}
        <span className={`shrink-0 w-1.5 h-1.5 rounded-full ${
          isLocked ? 'bg-cream-muted' : isSelected ? 'bg-cyan' : 'bg-success'
        }`} />
        <span className="truncate">{lab.name}</span>
        {isLocked && <LockIcon className="w-3 h-3 shrink-0 ml-auto" />}
      </button>

      {/* Actions (teacher/admin) */}
      {(role === 'teacher' || role === 'admin') && !lab.is_deleted && (
        <div className="relative shrink-0">
          <button
            onClick={(e) => { e.stopPropagation(); setShowMenu(!showMenu); }}
            className="opacity-0 group-hover:opacity-100 w-5 h-5 flex items-center justify-center text-cream-muted hover:text-cream rounded cursor-pointer transition-all"
          >
            <EllipsisIcon className="w-3 h-3" />
          </button>
          {showMenu && (
            <LabMenu
              lab={lab}
              onAction={(action) => { setShowMenu(false); onLabAction?.(action, lab); }}
              onClose={() => setShowMenu(false)}
            />
          )}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════
   CONTEXT MENUS
   ═══════════════════════════════════════════ */

function ClassMenu({ role, cls, onAction, onClose }) {
  return (
    <DropdownMenu onClose={onClose}>
      <MenuItem label="✏️ Rename" onClick={() => onAction('rename')} />
      <MenuItem label="🔑 Reset Code" onClick={() => onAction('reset-code')} />
      {role === 'admin' && (
        <MenuItem label="🔁 Transfer" onClick={() => onAction('transfer')} />
      )}
      <MenuItem label="🗑️ Archive" danger onClick={() => onAction('delete')} />
    </DropdownMenu>
  );
}

function LabMenu({ lab, onAction, onClose }) {
  return (
    <DropdownMenu onClose={onClose}>
      <MenuItem label="✏️ Rename" onClick={() => onAction('rename')} />
      <MenuItem
        label={lab.is_active ? '🔒 Lock' : '🔓 Unlock'}
        onClick={() => onAction('toggle')}
      />
      <MenuItem label="🗑️ Archive" danger onClick={() => onAction('delete')} />
    </DropdownMenu>
  );
}

function DropdownMenu({ children, onClose }) {
  return (
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div className="absolute right-0 top-6 w-36 bg-ink-surface border border-border-default rounded-lg shadow-elevated py-1 z-50 animate-scale-in">
        {children}
      </div>
    </>
  );
}

function MenuItem({ label, onClick, danger }) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2 text-xs font-medium transition-colors cursor-pointer ${
        danger ? 'text-danger hover:bg-danger-muted' : 'text-cream-secondary hover:text-cream hover:bg-ink-hover'
      }`}
    >
      {label}
    </button>
  );
}

/* ── Icons ── */

function PlusIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

function ChevronIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="m9 18 6-6-6-6" />
    </svg>
  );
}

function EllipsisIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <circle cx="5" cy="12" r="1.5" /><circle cx="12" cy="12" r="1.5" /><circle cx="19" cy="12" r="1.5" />
    </svg>
  );
}

function LockIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect width="18" height="11" x="3" y="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />
    </svg>
  );
}

function LogOutIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <polyline points="16 17 21 12 16 7" />
      <line x1="21" y1="12" x2="9" y2="12" />
    </svg>
  );
}

function SettingsIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}
