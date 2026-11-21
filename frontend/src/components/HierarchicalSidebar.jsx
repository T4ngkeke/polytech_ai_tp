/**
 * HierarchicalSidebar.jsx — Role-aware Class→Lab tree navigation for Edu-LLM.
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
 *   children                  — shown between the tree and the footer (student sessions on phones)
 *   loading: bool
 *   onHelp()                  — opens the guided tour (the ? button)
 *
 * ClassNode shape: { id, name, invite_code, labs: LabNode[] }
 * LabNode shape:   { id, name, is_active, is_deleted }
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import useAuthStore from '../store/authStore';
import ThemeToggle from './ThemeToggle';
import Icon from './Icon';
import { useT } from '../lib/i18n';

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
  onHelp,
  children,
}) {
  const t = useT();
  const { username, logout } = useAuthStore();
  const navigate = useNavigate();

  // Classes are expanded by default. Remember the ones the user collapsed: the
  // class list loads after the first render, so an "expanded" set built then
  // would be empty and every class would show collapsed after a reload.
  const [collapsedClassIds, setCollapsedClassIds] = useState(() => new Set());

  const handleSignOut = () => {
    logout();
    navigate('/login', { replace: true });
  };

  const toggleClass = (classId) => {
    setCollapsedClassIds((prev) => {
      const next = new Set(prev);
      if (next.has(classId)) next.delete(classId);
      else next.add(classId);
      return next;
    });
  };

  return (
    <div className="flex flex-col h-full min-h-0 bg-ink-base border-r border-border-subtle select-none">
      {/* Product mark, then the new class / join row */}
      <div className="flex h-12 shrink-0 items-center gap-2 px-3">
        <span className="flex h-6 w-6 items-center justify-center rounded-md bg-ink text-[13px] font-semibold text-on-ink">E</span>
        <span className="flex-1 text-[15px] font-semibold text-cream">Edu-LLM</span>
      </div>
      <div className="shrink-0 px-2 pb-1">
        {role === 'student' && (
          <button id="join-class-btn" data-tour="join-class" onClick={onJoinClass}
            className="flex h-9 w-full items-center gap-2.5 rounded-lg px-2.5 text-left text-[14px] text-cream hover:bg-ink-hover cursor-pointer">
            <Icon name="plus" className="h-4 w-4 shrink-0 text-cream-secondary" />{t('t.sb.join')}
          </button>
        )}
        {(role === 'teacher' || role === 'admin') && (
          <button id="create-class-btn" data-tour="new-class" onClick={onCreateClass}
            className="flex h-9 w-full items-center gap-2.5 rounded-lg px-2.5 text-left text-[14px] text-cream hover:bg-ink-hover cursor-pointer">
            <Icon name="plus" className="h-4 w-4 shrink-0 text-cream-secondary" />{t('t.newClass')}
          </button>
        )}
      </div>

      {/* Tree */}
      <div data-tour="classes" className="flex-1 overflow-y-auto px-2 py-1">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <div className="w-4 h-4 rounded-full border-2 border-cream-muted border-t-transparent animate-[spin_0.8s_linear_infinite]" />
          </div>
        ) : classes.length === 0 ? (
          <div className="text-center py-10 px-3">
            <p className="text-[13px] text-cream-muted">
              {role === 'student' ? t('t.sb.joinToStart') : t('t.sb.noClasses')}
            </p>
          </div>
        ) : (
          classes.map((cls) => (
            <ClassNode
              key={cls.id}
              cls={cls}
              role={role}
              isExpanded={!collapsedClassIds.has(cls.id)}
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

      {children}

      {/* Footer: user row and the buttons */}
      <div className="shrink-0 border-t border-border-subtle bg-ink-base px-3 py-2.5">
        <div className="flex items-center gap-2.5">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-ink text-[13px] font-medium text-on-ink">
            {(username || '?').slice(0, 1).toUpperCase()}
          </span>
          <span className="min-w-0 flex-1 truncate text-[14px] text-cream">{username || t('t.sb.user')}</span>
          <div className="flex shrink-0 items-center gap-0.5">
            {onHelp && (
              <button onClick={onHelp} data-tour="help" title={t('t.sb.tour')} aria-label={t('t.sb.tour')}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-cream-secondary hover:bg-ink-hover hover:text-cream cursor-pointer">
                <Icon name="help" className="h-4 w-4" />
              </button>
            )}
            <ThemeToggle className="!border-transparent !bg-transparent hover:!bg-ink-hover hover:!text-cream" />
            <button onClick={() => navigate('/account')} title={t('t.sb.account')} aria-label={t('t.sb.account')}
              className="flex h-8 w-8 items-center justify-center rounded-lg text-cream-secondary hover:bg-ink-hover hover:text-cream cursor-pointer">
              <Icon name="settings" className="h-4 w-4" />
            </button>
            <button onClick={handleSignOut} title={t('logout')} aria-label={t('logout')}
              className="flex h-8 w-8 items-center justify-center rounded-lg text-cream-secondary hover:bg-ink-hover hover:text-danger cursor-pointer">
              <Icon name="logout" className="h-4 w-4" />
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
  const t = useT();
  const [showMenu, setShowMenu] = useState(false);
  const hasLabs = cls.labs && cls.labs.length > 0;

  return (
    <div className="mb-0.5">
      {/* Class row */}
      <div
        role="button"
        tabIndex={0}
        aria-expanded={isExpanded}
        onClick={() => { onToggle(); onSelectClass?.(cls); }}
        onKeyDown={(e) => {
          // The row holds the actions button, so it cannot itself be a <button>.
          if (e.target !== e.currentTarget || (e.key !== 'Enter' && e.key !== ' ')) return;
          e.preventDefault();
          onToggle();
          onSelectClass?.(cls);
        }}
        className="group flex min-h-9 items-center gap-1 px-2 py-1 rounded-lg hover:bg-ink-hover transition-colors cursor-pointer text-left focus-visible:outline focus-visible:outline-cream-muted"
      >
        {/* Expand toggle */}
        <span className="shrink-0 w-4 h-4 flex items-center justify-center text-cream-muted group-hover:text-cream transition-colors">
          <Icon name="chevronRight" className={`w-3 h-3 transition-transform ${isExpanded ? 'rotate-90' : ''}`} />
        </span>

        {/* Class name */}
        <span className="flex-1 min-w-0 text-left text-[14px] font-medium text-cream truncate">
          {cls.name}
        </span>

        {/* Invite code badge (student/teacher) */}
        {role !== 'admin' && cls.invite_code && (
          <code className="shrink-0 rounded-full bg-ink-hover px-2 py-0.5 font-mono text-[11px] text-cream-muted">
            {cls.invite_code}
          </code>
        )}

        {/* Actions menu (teacher/admin) */}
        {(role === 'teacher' || role === 'admin') && (
          <div className="relative shrink-0">
            <button
              onClick={(e) => { e.stopPropagation(); setShowMenu(!showMenu); }}
              aria-label={t('t.sb.classActions', { name: cls.name })} title={t('t.sb.classActionsTitle')}
              className="md:opacity-0 md:group-hover:opacity-100 focus-visible:opacity-100 w-7 h-7 flex items-center justify-center text-cream-muted hover:text-cream hover:bg-ink-hover rounded transition-all cursor-pointer"
            >
              <Icon name="ellipsis" className="w-3.5 h-3.5" />
            </button>
            {showMenu && (
              <ClassMenu
                role={role}
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
            <p className="text-[13px] text-cream-muted px-2 py-1">{t('t.sb.noLabs')}</p>
          ) : (
            cls.labs.map((lab) => (
              <LabNode
                key={lab.id}
                lab={lab}
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
              className="w-full text-left text-[13px] text-cream-muted hover:text-cream hover:bg-ink-hover px-2 py-1.5 rounded-lg transition-colors cursor-pointer"
            >
              + {t('t.sb.addLab')}
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

function LabNode({ lab, role, isSelected, onSelect, onLabAction }) {
  const t = useT();
  const [showMenu, setShowMenu] = useState(false);
  const isLocked = lab.is_deleted || !lab.is_active;
  // A locked lab is closed to students only: its teacher opens it to unlock it
  // or to read its conversations.
  const closed = lab.is_deleted || (isLocked && role === 'student');

  return (
    <div className="group relative flex items-center">
      <button
        onClick={!closed ? onSelect : undefined}
        disabled={closed}
        title={isLocked && !closed ? t('t.sb.lockedHint') : undefined}
        className={`flex-1 min-w-0 flex items-center gap-2 px-2 py-1.5 rounded-lg text-[14px] transition-colors cursor-pointer text-left
          ${isSelected
            ? 'bg-ink-hover text-cream'
            : closed
              ? 'text-cream-muted opacity-50 cursor-not-allowed'
              : isLocked
                ? 'text-cream-muted hover:text-cream hover:bg-ink-hover'
                : 'text-cream-secondary hover:text-cream hover:bg-ink-hover'
          }`}
      >
        <span className="truncate">{lab.name}</span>
        {isLocked && <Icon name="lock" className="w-3 h-3 shrink-0 ml-auto" />}
      </button>

      {/* Actions (teacher/admin) */}
      {(role === 'teacher' || role === 'admin') && !lab.is_deleted && (
        <div className="relative shrink-0">
          <button
            onClick={(e) => { e.stopPropagation(); setShowMenu(!showMenu); }}
            aria-label={t('t.sb.labActions', { name: lab.name })} title={t('t.sb.labActionsTitle')}
            className="md:opacity-0 md:group-hover:opacity-100 focus-visible:opacity-100 w-7 h-7 flex items-center justify-center text-cream-muted hover:text-cream hover:bg-ink-hover rounded cursor-pointer transition-all"
          >
            <Icon name="ellipsis" className="w-3.5 h-3.5" />
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

function ClassMenu({ role, onAction, onClose }) {
  const t = useT();
  return (
    <DropdownMenu onClose={onClose}>
      <MenuItem icon="pencil" label={t('t.rename')} onClick={() => onAction('rename')} />
      <MenuItem icon="key" label={t('t.sb.resetCode')} onClick={() => onAction('reset-code')} />
      {role === 'admin' && (
        <MenuItem icon="transfer" label={t('t.sb.transfer')} onClick={() => onAction('transfer')} />
      )}
      <MenuItem icon="archive" label={t('t.archive')} danger onClick={() => onAction('delete')} />
    </DropdownMenu>
  );
}

function LabMenu({ lab, onAction, onClose }) {
  const t = useT();
  return (
    <DropdownMenu onClose={onClose}>
      <MenuItem icon="pencil" label={t('t.rename')} onClick={() => onAction('rename')} />
      <MenuItem
        icon={lab.is_active ? 'lock' : 'unlock'}
        label={lab.is_active ? t('t.lock') : t('t.unlock')}
        onClick={() => onAction('toggle')}
      />
      <MenuItem icon="archive" label={t('t.archive')} danger onClick={() => onAction('delete')} />
    </DropdownMenu>
  );
}

function DropdownMenu({ children, onClose }) {
  return (
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div className="absolute right-0 top-6 w-44 rounded-[14px] border border-border-subtle bg-ink-raised p-1.5 shadow-elevated z-50 animate-scale-in">
        {children}
      </div>
    </>
  );
}

function MenuItem({ icon, label, onClick, danger }) {
  return (
    <button
      onClick={onClick}
      className={`flex h-9 w-full items-center gap-2.5 rounded-lg text-left px-2.5 text-[14px] transition-colors cursor-pointer ${
        danger ? 'text-danger hover:bg-ink-hover' : 'text-cream hover:bg-ink-hover'
      }`}
    >
      {icon && <Icon name={icon} className="h-4 w-4 shrink-0 text-cream-secondary" />}
      {label}
    </button>
  );
}
