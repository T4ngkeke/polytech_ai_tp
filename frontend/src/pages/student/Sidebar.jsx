/**
 * Sidebar.jsx — the student's left column (2026-11 redesign): product mark and
 * bell, new chat, search, the class › lab picker, the conversations of the lab
 * (pinned, today, yesterday, last 7 days, older) with rename / pin / export /
 * delete, the day and week meters, and the user menu.
 */
import { useRef, useState } from 'react';
import Icon from '../../components/Icon';
import { useT } from '../../lib/i18n';
import { percent } from '../../lib/quota';
import { IconButton, MenuItem, Meter, menuClass, useDismiss } from './ui';
import Notifications from './Notifications';

/** Groups of sessions: pinned first, then by age of the conversation. */
export function groupSessions(sessions, now = new Date()) {
  const startOfDay = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const day = 86_400_000;
  const groups = { pinned: [], today: [], yesterday: [], week: [], older: [] };
  for (const s of sessions) {
    const at = new Date(s.last_activity_at || s.created_at).getTime();
    if (s.pinned) groups.pinned.push(s);
    else if (at >= startOfDay) groups.today.push(s);
    else if (at >= startOfDay - day) groups.yesterday.push(s);
    else if (at >= startOfDay - 6 * day) groups.week.push(s);
    else groups.older.push(s);
  }
  return Object.entries(groups).filter(([, list]) => list.length);
}

function CoursePicker({ classes, activeLabId, onSelectLab, onJoin }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useDismiss(ref, open, () => setOpen(false));
  const activeClass = classes.find((c) => c.labs?.some((l) => l.id === activeLabId));
  const activeLab = activeClass?.labs.find((l) => l.id === activeLabId);
  return (
    <div ref={ref} className="relative" data-tour="classes">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
        className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left hover:bg-ink-hover">
        <Icon name="book" className="h-4 w-4 shrink-0 text-cream-secondary" />
        <span className="min-w-0 flex-1">
          {activeLab ? (
            <>
              <span className="block truncate text-[12px] text-cream-muted">{activeClass.name}</span>
              <span className="block truncate text-[14px] text-cream">{activeLab.name}</span>
            </>
          ) : (
            <span className="block truncate text-[14px] text-cream-secondary">
              {classes.length ? t('picker.choose') : t('picker.noClass')}
            </span>
          )}
        </span>
        <Icon name="chevronDown" className="h-4 w-4 shrink-0 text-cream-muted" />
      </button>
      {open && (
        <div className={`absolute left-0 right-0 top-full z-30 mt-1 max-h-[60vh] overflow-y-auto ${menuClass}`}>
          {classes.map((c) => (
            <div key={c.id} className="mb-1">
              <div className="flex items-center gap-2 px-2.5 pb-1 pt-2 text-[12px] font-medium text-cream-muted">
                <span className="truncate">{c.name}</span>
                {c.exam_mode && <span className="rounded bg-warn-soft px-1.5 text-[11px] text-gold">{t('exam.short')}</span>}
              </div>
              {(c.labs || []).map((lab) => (
                <MenuItem key={lab.id} icon={lab.is_active ? null : 'lock'} label={lab.name}
                  active={lab.id === activeLabId} disabled={!lab.is_active}
                  title={lab.is_active ? undefined : t('lab.locked')}
                  onClick={() => { setOpen(false); onSelectLab(lab.id); }}>
                  {lab.id === activeLabId && <Icon name="check" className="h-4 w-4 text-cream-secondary" />}
                </MenuItem>
              ))}
              {!c.labs?.length && <p className="px-2.5 py-1 text-[13px] text-cream-muted">{t('picker.noLab')}</p>}
            </div>
          ))}
          <div className="my-1 border-t border-border-subtle" />
          <MenuItem icon="plus" label={t('join.title')} onClick={() => { setOpen(false); onJoin(); }} />
        </div>
      )}
    </div>
  );
}

function SessionRow({ session, active, onSelect, onRename, onPin, onExport, onDelete }) {
  const t = useT();
  const [menu, setMenu] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [title, setTitle] = useState('');
  const ref = useRef(null);
  useDismiss(ref, menu, () => { setMenu(false); setConfirming(false); });
  const name = session.title || t('session.untitled');
  if (renaming) {
    return (
      <form className="px-1" onSubmit={(e) => { e.preventDefault(); setRenaming(false); onRename(session.id, title); }}>
        <input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} aria-label={t('session.rename')}
          onBlur={() => { setRenaming(false); if (title.trim() && title !== session.title) onRename(session.id, title); }}
          onKeyDown={(e) => { if (e.key === 'Escape') setRenaming(false); }}
          className="w-full rounded-lg border border-border-default bg-ink-raised px-2 py-1.5 text-[14px] text-cream outline-none" />
      </form>
    );
  }
  return (
    <div ref={ref} className={`group relative flex items-center rounded-lg ${active ? 'bg-ink-hover' : 'hover:bg-ink-hover'}`}>
      <button type="button" onClick={() => onSelect(session.id)} title={name}
        className="min-w-0 flex-1 truncate px-2.5 py-1.5 text-left text-[14px] text-cream">
        {name}
      </button>
      <button type="button" onClick={() => setMenu(!menu)} aria-label={t('session.actions')}
        className={`mr-1 rounded-md p-1 text-cream-muted hover:text-cream ${menu || active ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus:opacity-100'}`}>
        <Icon name="ellipsis" className="h-4 w-4" />
      </button>
      {menu && (
        <div className={`absolute right-0 top-full z-30 mt-1 w-56 ${menuClass}`}>
          {confirming ? (
            <div className="p-2">
              <p className="mb-2 text-[13px] text-cream-secondary">{t('session.deleteConfirm')}</p>
              <div className="flex gap-2">
                <button type="button" onClick={() => { setMenu(false); onDelete(session.id); }}
                  className="rounded-lg bg-danger px-3 py-1.5 text-[13px] text-white">{t('delete')}</button>
                <button type="button" onClick={() => setConfirming(false)}
                  className="rounded-lg px-3 py-1.5 text-[13px] text-cream-secondary hover:bg-ink-hover">{t('cancel')}</button>
              </div>
            </div>
          ) : (
            <>
              <MenuItem icon="pencil" label={t('session.rename')}
                onClick={() => { setMenu(false); setTitle(session.title || ''); setRenaming(true); }} />
              <MenuItem icon={session.pinned ? 'unpin' : 'pin'} label={t(session.pinned ? 'session.unpin' : 'session.pin')}
                onClick={() => { setMenu(false); onPin(session.id, !session.pinned); }} />
              <MenuItem icon="download" label={t('session.export')} onClick={() => { setMenu(false); onExport(session.id); }} />
              <MenuItem icon="trash" label={t('delete')} danger onClick={() => setConfirming(true)} />
            </>
          )}
        </div>
      )}
    </div>
  );
}

function UserMenu({ username, onSettings, onHelp, onLogout }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useDismiss(ref, open, () => setOpen(false));
  return (
    <div ref={ref} className="relative" data-tour="help">
      <button type="button" onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2.5 rounded-lg px-2 py-2 hover:bg-ink-hover">
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-ink text-[13px] font-medium text-on-ink">
          {(username || '?').slice(0, 1).toUpperCase()}
        </span>
        <span className="min-w-0 flex-1 truncate text-left text-[14px] text-cream">{username}</span>
      </button>
      {open && (
        <div className={`absolute bottom-full left-0 right-0 z-30 mb-1 ${menuClass}`}>
          <MenuItem icon="settings" label={t('settings.title')} onClick={() => { setOpen(false); onSettings(); }} />
          <MenuItem icon="help" label={t('help')} onClick={() => { setOpen(false); onHelp(); }} />
          <MenuItem icon="logout" label={t('logout')} onClick={() => { setOpen(false); onLogout(); }} />
        </div>
      )}
    </div>
  );
}

export default function Sidebar({
  classes, activeLabId, onSelectLab, onJoin, sessions, activeSessionId, onSelectSession, onNew,
  onRename, onPin, onExport, onDelete, usage, username, onSettings, onHelp, onLogout, onSearch,
  onCollapse, onOpenUsage,
}) {
  const t = useT();
  const groups = groupSessions(sessions);
  const groupName = { pinned: 'group.pinned', today: 'group.today', yesterday: 'group.yesterday',
    week: 'group.week', older: 'group.older' };
  const day = percent(usage.used, usage.limit);
  const week = percent(usage.week_used, usage.week_limit);
  return (
    <div className="flex h-full flex-col bg-ink-base">
      <div className="flex h-12 shrink-0 items-center gap-2 px-3">
        <span className="flex h-6 w-6 items-center justify-center rounded-md bg-ink text-[13px] font-semibold text-on-ink">E</span>
        <span className="flex-1 text-[15px] font-semibold text-cream">Edu-LLM</span>
        <Notifications />
        {onCollapse && <IconButton icon="sidebar" label={t('sidebar.close')} onClick={onCollapse} />}
      </div>
      <div className="shrink-0 space-y-0.5 px-2">
        <MenuItem icon="newChat" label={t('chat.new')} onClick={onNew} disabled={!activeLabId} />
        <MenuItem icon="search" label={t('search.title')} hint="Ctrl K" onClick={onSearch} />
        <div className="pt-1"><CoursePicker classes={classes} activeLabId={activeLabId} onSelectLab={onSelectLab} onJoin={onJoin} /></div>
      </div>
      <nav className="mt-2 min-h-0 flex-1 overflow-y-auto px-2 pb-2" aria-label={t('sessions')} data-tour="sessions">
        {groups.map(([key, list]) => (
          <div key={key} className="mb-3">
            <div className="px-2.5 pb-1 pt-2 text-[12px] font-medium text-cream-muted">{t(groupName[key])}</div>
            {list.map((s) => (
              <SessionRow key={s.id} session={s} active={s.id === activeSessionId} onSelect={onSelectSession}
                onRename={onRename} onPin={onPin} onExport={onExport} onDelete={onDelete} />
            ))}
          </div>
        ))}
        {activeLabId && !sessions.length && (
          <p className="px-2.5 py-2 text-[13px] text-cream-muted">{t('sessions.none')}</p>
        )}
      </nav>
      <div className="shrink-0 border-t border-border-subtle px-3 pb-2 pt-3">
        <button type="button" onClick={onOpenUsage} title={t('quota.sentence')} data-tour="quota"
          className="mb-2 block w-full space-y-2 rounded-lg px-1 py-1 text-left hover:bg-ink-hover">
          <Meter label={t('quota.today')} value={day} />
          <Meter label={t('quota.week')} value={week} />
        </button>
        <UserMenu username={username} onSettings={onSettings} onHelp={onHelp} onLogout={onLogout} />
      </div>
    </div>
  );
}
