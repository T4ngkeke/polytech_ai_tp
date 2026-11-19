/**
 * chat/SessionsPanel.jsx — Session list of the chat page: new session, select, rename, soft delete.
 */
import Icon from '../../components/Icon';

export default function SessionsPanel({
  sessions, activeSessionId, onNewSession, onSelectSession,
  renamingSessionId, setRenamingSessionId, renameTitle, setRenameTitle, onRenameSession,
  confirmingDeleteId, setConfirmingDeleteId, onDeleteSession,
}) {
  return (
    <>
      <div className="px-3 pt-4 pb-2 border-b border-border-subtle">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-cream-muted mb-2">Sessions</p>
        <button
          id="new-session-btn"
          data-tour="new-session"
          onClick={onNewSession}
          className="w-full text-xs py-1.5 px-2 rounded-lg border border-border-default text-cream-secondary hover:text-cyan hover:border-cyan/30 hover:bg-cyan-muted transition-all cursor-pointer"
        >
          + New Session
        </button>
      </div>
      <div className="flex-1 overflow-y-auto py-1">
        {sessions.map((s) => (
          <div key={s.id} className="group relative">
            {renamingSessionId === s.id ? (
              <input
                autoFocus
                value={renameTitle}
                onChange={(e) => setRenameTitle(e.target.value)}
                onBlur={() => onRenameSession(s.id)}
                onKeyDown={(e) => { if (e.key === 'Enter') onRenameSession(s.id); if (e.key === 'Escape') setRenamingSessionId(null); }}
                className="w-full px-3 py-2 text-xs bg-ink-hover text-cream border-none outline-none"
              />
            ) : confirmingDeleteId === s.id ? (
              <div className="flex items-center gap-2 px-3 py-2 text-xs bg-danger-deep">
                <span className="flex-1 truncate text-danger">Delete this chat?</span>
                <button
                  onClick={() => onDeleteSession(s.id)}
                  className="w-7 h-7 rounded flex items-center justify-center text-danger hover:text-danger-hover hover:bg-ink-hover transition-colors cursor-pointer"
                  title="Confirm delete"
                >
                  <Icon name="check" className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => setConfirmingDeleteId(null)}
                  className="w-7 h-7 rounded flex items-center justify-center text-cream-muted hover:text-cream hover:bg-ink-hover transition-colors cursor-pointer"
                  title="Cancel"
                >
                  <Icon name="close" className="w-3.5 h-3.5" />
                </button>
              </div>
            ) : (
              <>
                <button
                  onClick={() => onSelectSession(s.id)}
                  className={`w-full text-left px-3 py-2 pr-16 text-xs transition-all cursor-pointer ${
                    activeSessionId === s.id
                      ? 'bg-cyan-muted text-cyan'
                      : 'text-cream-secondary hover:bg-ink-hover hover:text-cream'
                  }`}
                >
                  <span className="block truncate">{s.title || 'New conversation'}</span>
                  <span className="block text-[9px] text-cream-muted mt-0.5">
                    {new Date(s.created_at).toLocaleDateString()}
                  </span>
                </button>
                {/* Hover actions: rename + soft-delete */}
                <div className="absolute right-1.5 top-1/2 -translate-y-1/2 flex items-center gap-0.5 md:opacity-0 md:group-hover:opacity-100 transition-opacity">
                  <button
                    onClick={() => { setRenamingSessionId(s.id); setRenameTitle(s.title || ''); }}
                    className="w-7 h-7 rounded flex items-center justify-center text-cream-muted hover:text-cream hover:bg-ink-hover transition-colors cursor-pointer"
                    title="Rename session"
                  >
                    <Icon name="pencil" className="w-3.5 h-3.5" />
                  </button>
                  <button
                    onClick={() => setConfirmingDeleteId(s.id)}
                    className="w-7 h-7 rounded flex items-center justify-center text-cream-muted hover:text-danger hover:bg-ink-hover transition-colors cursor-pointer"
                    title="Delete session"
                  >
                    <Icon name="trash" className="w-3.5 h-3.5" />
                  </button>
                </div>
              </>
            )}
          </div>
        ))}
      </div>
    </>
  );
}
