/**
 * StudentChat.jsx — the student's chat page (2026-11 redesign, approved demo
 * of 2026-11-20). Layout: sidebar | chat | cited page (when open).
 *
 * One entry: every question goes to the research agent, which decides
 * whether to search the course (backend routers/chat.py). The "+" menu adds
 * files, a screenshot, course files, earlier chats, and the tools the lab
 * allows (web search, Python in this browser, deep research). Questions can be
 * edited or asked again as a new version of the last turn (version_of).
 *
 * Deleting a conversation is a soft delete: the teacher still sees it.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import useAuthStore from '../../store/authStore';
import { useT } from '../../lib/i18n';
import { nearLimit, resetNotice } from '../../lib/quota';
import { useChatScroll } from '../../lib/chatScroll';
import { downloadText } from '../../lib/exportFile';
import useMediaQuery from '../../lib/useMediaQuery';
import Icon from '../../components/Icon';
import Tour from '../../components/Tour';
import { studentTour, tourButtons } from './tour';
import Sidebar from './Sidebar';
import Composer from './Composer';
import SourcePanel from './SourcePanel';
import SettingsModal from './SettingsModal';
import SearchModal from './SearchModal';
import JoinModal from './JoinModal';
import { preloadContent } from './Turns';
import Thread from './Thread';
import useChat from './useChat';
import useConversationSync from './useConversationSync';

const REFRESH_MS = 90_000;
const MIN_GAP_MS = 10_000;

/** Refresh `load` now and then while the page is visible, and when the student comes back. */
function useRefresh(load) {
  useEffect(() => {
    let last = Date.now();
    const refresh = () => {
      if (document.visibilityState !== 'visible' || Date.now() - last < MIN_GAP_MS) return;
      last = Date.now();
      load();
    };
    const id = setInterval(refresh, REFRESH_MS);
    document.addEventListener('visibilitychange', refresh);
    window.addEventListener('focus', refresh);
    return () => { clearInterval(id); document.removeEventListener('visibilitychange', refresh); window.removeEventListener('focus', refresh); };
  }, [load]);
}

function untilText(t, iso) {
  const ms = new Date(iso).getTime() - Date.now();
  if (!(ms > 0)) return t('time.soon');
  const minutes = Math.ceil(ms / 60000);
  if (minutes < 60) return t('time.minutes', { n: minutes });
  const hours = Math.round(minutes / 60);
  return hours < 48 ? t('time.hours', { n: hours }) : t('time.days', { n: Math.round(hours / 24) });
}

function sessionMarkdown(t, title, messages) {
  const lines = [`# ${title || t('session.untitled')}`, ''];
  for (const m of messages.filter((x) => !x.superseded_at)) {
    lines.push(`## ${m.sender === 'user' ? t('export.student') : 'Edu-LLM'}`, '', m.content || '', '');
  }
  return lines.join('\n');
}

export default function StudentChat() {
  const t = useT();
  const { labId: urlLabId } = useParams();
  const navigate = useNavigate();
  const { username, tourSeen, markTourSeen, logout } = useAuthStore();
  const isWide = useMediaQuery('(min-width: 900px)');

  const [classes, setClasses] = useState([]);
  const [activeLabId, setActiveLabId] = useState(urlLabId || null);
  const [sessions, setSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [usage, setUsage] = useState({ used: 0, limit: 0, week_used: 0, week_limit: 0, blocked: null });
  const [resetAsked, setResetAsked] = useState(false);
  const [input, setInput] = useState('');
  const [tools, setTools] = useState([]);
  const [preview, setPreview] = useState(null);
  // A cited page the student opens is counted for the teacher's Analytics
  // (citation_opens); a failed count changes nothing for the student.
  const openSource = useCallback((source) => {
    setPreview(source);
    if (source?.messageId && source.documentId) {
      api.post(`/api/chat/messages/${source.messageId}/citation-opened`,
        { document_id: source.documentId, page_no: source.page ?? null }).catch(() => {});
    }
  }, []);
  const [navOpen, setNavOpen] = useState(false);
  const [sidebarHidden, setSidebarHidden] = useState(false);
  const [modal, setModal] = useState(null);       // null | 'join' | 'search' | {settings: tab}
  const [tourOpen, setTourOpen] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [historyStart, setHistoryStart] = useState(null);
  const [seenReset, setSeenReset] = useState(0);
  const listRef = useRef(null);
  const inputRef = useRef(null);
  const composerRef = useRef(null);
  const restoredRef = useRef(false);
  const { onScroll, update: updateScroll, follow } = useChatScroll(listRef);

  useEffect(() => { if (!tourSeen) setTourOpen(true); }, [tourSeen]);
  useEffect(() => { preloadContent(); }, []);

  // ── Classes, labs, usage ──
  const loadClasses = useCallback(async () => {
    try {
      const data = await api.get('/api/student/classes');
      setClasses(await Promise.all(data.map(async (c) => ({
        ...c, labs: await api.get(`/api/student/classes/${c.id}/labs`).catch(() => []) }))));
    } catch { /* keeps the last list */ }
  }, []);
  const loadUsage = useCallback(async () => {
    try {
      const data = await api.get('/api/student/usage');
      setUsage(data);
      const near = nearLimit(data);
      if (near) {
        let seen = false;
        try { seen = localStorage.getItem(near.key) === '1'; localStorage.setItem(near.key, '1'); } catch { /* private window */ }
        if (!seen) toast(t(near.kind === 'week' ? 'quota.near.week' : 'quota.near.day'), { duration: 8000 });
      }
    } catch { /* keeps the last values */ }
  }, [t]);
  useEffect(() => { loadClasses(); loadUsage(); }, [loadClasses, loadUsage]);
  useRefresh(loadClasses);
  useRefresh(loadUsage);

  const activeClass = classes.find((c) => c.labs?.some((l) => l.id === activeLabId));
  const activeLab = activeClass?.labs.find((l) => l.id === activeLabId);
  const examMode = !!activeClass?.exam_mode;
  const features = useMemo(() => activeLab?.features || {}, [activeLab]);
  // A tool the lab no longer allows (the teacher changed it, exam mode) is turned off.
  useEffect(() => {
    setTools((list) => {
      const kept = list.filter((k) => features[k] && !examMode);
      return kept.length === list.length ? list : kept;
    });
  }, [features, examMode]);

  // ── The conversation (useChat: sending, stream events, Python runs, versions) ──
  const syncRef = useRef(null);
  const chat = useChat({
    tools, follow,
    getSessionId: async () => {
      if (activeSessionId) return activeSessionId;
      if (!activeLabId) return null;
      try {
        const created = await api.post(`/api/student/labs/${activeLabId}/sessions`, {});
        setSessions((prev) => [created, ...prev]);
        setActiveSessionId(created.id);
        return created.id;
      } catch (err) {
        toast.error(err.message || t('error.newChat'));
        return null;
      }
    },
    onDone: loadUsage,
    onTitle: (sessionId, title) => setSessions((prev) => prev.map((s) => (s.id === sessionId ? { ...s, title } : s))),
    // The teacher may have turned exam mode on or off since the page loaded.
    onStatus: (action) => { if (activeClass && action.examMode !== !!activeClass.exam_mode) loadClasses(); },
    onRefusal: (refusal, text, isVersion) => {
      // Nothing was asked: the question goes back into the box.
      if (!isVersion) setInput((current) => current || text);
      if (refusal.reload === 'classes') loadClasses();
      if (refusal.reload === 'usage') loadUsage();
      if (refusal.reload === 'conversation') syncRef.current?.checkNow();
    },
  });
  const { messages, setMessages, streaming, send, stop, reset } = chat;

  // ── The same conversation in the student's other windows (useConversationSync) ──
  const loadMessages = useCallback(async (sessionId) => (await api.get(`/api/student/sessions/${sessionId}`)).messages || [], []);
  const sync = useConversationSync({ sessionId: activeSessionId, streaming, messages, setMessages, load: loadMessages });
  syncRef.current = sync;

  // ── Lab and sessions ──
  const loadSession = useCallback(async (sessionId) => {
    reset();
    setActiveSessionId(sessionId);
    setPreview(null);
    follow('tail');
    setLoadingMessages(true);
    try {
      const data = await api.get(`/api/student/sessions/${sessionId}`);
      setMessages(data.messages || []);
    } catch {
      toast.error(t('error.loadMessages'));
    } finally {
      setLoadingMessages(false);
    }
  }, [follow, t, reset, setMessages]);

  const selectLab = useCallback(async (labId, { openLatest = false } = {}) => {
    restoredRef.current = true;
    reset();
    setActiveLabId(labId);
    setActiveSessionId(null);
    setPreview(null);
    setNavOpen(false);
    navigate(`/chat/${labId}`, { replace: true });
    try {
      const list = await api.get(`/api/student/sessions?lab_id=${labId}`);
      setSessions(list);
      if (openLatest && list.length) loadSession(list[0].id);
    } catch {
      setSessions([]);
    }
  }, [navigate, loadSession, reset]);

  useEffect(() => {
    if (urlLabId && !restoredRef.current) selectLab(urlLabId, { openLatest: true });
  }, [urlLabId, selectLab]);
  // No lab in the address: open the first open lab.
  useEffect(() => {
    if (urlLabId || restoredRef.current || !classes.length) return;
    const first = classes.flatMap((c) => c.labs || []).find((l) => l.is_active);
    if (first) selectLab(first.id);
  }, [classes, urlLabId, selectLab]);

  const newChat = useCallback(() => {
    reset();
    setActiveSessionId(null);
    setPreview(null);
    setNavOpen(false);
    setTimeout(() => inputRef.current?.focus(), 0);
  }, [reset]);

  // Exam mode on: the conversations from before it are hidden; off: they are
  // back. Reload the list when it changes, and leave a conversation now hidden.
  const examSeen = useRef(examMode);
  useEffect(() => {
    if (examSeen.current === examMode) return;
    examSeen.current = examMode;
    if (!activeLabId) return;
    api.get(`/api/student/sessions?lab_id=${activeLabId}`).then((list) => {
      setSessions(list);
      if (activeSessionId && !list.some((s) => s.id === activeSessionId)) newChat();
    }).catch(() => {});
  }, [examMode, activeLabId, activeSessionId, newChat]);

  useEffect(() => { updateScroll(); }, [messages, updateScroll]);

  // Where the history the assistant sees starts (older messages are summarized).
  useEffect(() => {
    if (!activeSessionId || streaming) return undefined;
    let current = true;
    api.get(`/api/chat/sessions/${activeSessionId}/history-start`)
      .then((start) => { if (current) setHistoryStart({ ...start, sessionId: activeSessionId }); })
      .catch(() => {});
    return () => { current = false; };
  }, [activeSessionId, streaming]);

  // Ctrl/Cmd+K opens the search.
  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setModal('search'); }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);


  // ── Session actions ──
  const renameSession = async (id, title) => {
    if (!title.trim()) return;
    try {
      const updated = await api.put(`/api/student/sessions/${id}`, { title });
      setSessions((prev) => prev.map((s) => (s.id === id ? { ...s, title: updated.title } : s)));
    } catch (err) { toast.error(err.message); }
  };
  const pinSession = async (id, pinned) => {
    try {
      const updated = await api.put(`/api/student/sessions/${id}`, { pinned });
      setSessions((prev) => prev.map((s) => (s.id === id ? { ...s, pinned: updated.pinned } : s)));
    } catch (err) { toast.error(err.message); }
  };
  const deleteSession = async (id) => {
    try { await api.delete(`/api/student/sessions/${id}`); } catch (err) { toast.error(err.message); return; }
    setSessions((prev) => prev.filter((s) => s.id !== id));
    if (id === activeSessionId) newChat();
    toast.success(t('session.deleted'));
  };
  const exportSession = async (id) => {
    try {
      const data = await api.get(`/api/student/sessions/${id}`);
      const title = sessions.find((s) => s.id === id)?.title;
      downloadText(`${(title || 'conversation').replace(/[^\w\- ]+/g, '').slice(0, 60) || 'conversation'}.md`,
        sessionMarkdown(t, title, data.messages || []), 'text/markdown');
    } catch (err) { toast.error(err.message); }
  };

  const askForReset = async () => {
    try {
      const data = await api.post('/api/student/usage/reset-request');
      setUsage(data);
      setResetAsked(true);
    } catch (err) { toast.error(err.message); loadUsage(); }
  };

  // ── Derived ──
  const locked = activeLab && !activeLab.is_active;
  const blocked = usage.blocked;
  const disabledReason = !activeLabId ? 'noLab' : locked ? 'locked' : blocked ? 'quota'
    : sync.elsewhere && !streaming ? 'elsewhere' : null;
  const resetKey = resetNotice(usage);
  const showReset = (() => { try { return resetKey && localStorage.getItem(resetKey) !== '1'; } catch { return false; } })();
  void seenReset;
  const hidden = historyStart?.sessionId === activeSessionId ? historyStart : null;
  const welcome = !messages.length && !loadingMessages;

  const composer = (
    <Composer ref={composerRef} value={input} onChange={setInput}
      onSend={(text, attachments) => { send(text, attachments); setInput(''); }}
      onStop={stop} streaming={streaming} labId={activeLabId} features={features} examMode={examMode}
      sessions={sessions} activeSessionId={activeSessionId} tools={tools} setTools={setTools}
      disabledReason={disabledReason} inputRef={inputRef} centered={welcome} />
  );

  const banners = (
    <>
      {sync.elsewhere && !streaming && (
        <div role="status" className="mb-2 flex items-center gap-3 rounded-xl bg-warn-soft px-3 py-2 text-[13px] text-gold">
          <span className="flex-1">{t('chat.elsewhere')}</span>
          <button type="button" onClick={sync.stopElsewhere}
            className="shrink-0 cursor-pointer rounded-full border border-current px-2.5 py-0.5 text-[12.5px] hover:bg-ink-raised">
            {t('chat.stopElsewhere')}
          </button>
        </div>
      )}
      {showReset && (
        <div role="status" className="mb-2 flex items-center gap-3 rounded-2xl border border-success/30 bg-success/10 px-4 py-2.5 text-[13.5px] text-cream">
          <span className="flex-1">{t('quota.resetDone')}</span>
          <button type="button" aria-label={t('close')} onClick={() => { try { localStorage.setItem(resetKey, '1'); } catch { /* */ } setSeenReset((n) => n + 1); }}
            className="rounded p-1 text-cream-muted hover:text-cream"><Icon name="close" className="h-4 w-4" /></button>
        </div>
      )}
      {blocked && (
        <div role="status" className="mb-2 rounded-2xl bg-warn-soft px-4 py-3 text-[13.5px] text-cream">
          <p>
            {t(blocked === 'week' ? 'quota.blocked.week' : 'quota.blocked.day',
              { when: untilText(t, blocked === 'week' ? (usage.week_resets_at || usage.resets_at) : usage.resets_at) })}
            {usage.reset_state === 'ignored' && ` ${t('quota.ignored')}`}
          </p>
          {usage.reset_state !== 'ignored' && (
            usage.reset_requested || resetAsked
              ? <p className="mt-1 text-cream-secondary">{t('quota.requestSent')}</p>
              : <button type="button" onClick={askForReset} className="mt-2 rounded-full bg-ink px-3.5 py-1.5 text-[13px] text-on-ink">{t('quota.askReset')}</button>
          )}
        </div>
      )}
      {locked && <div role="status" className="mb-2 rounded-2xl bg-warn-soft px-4 py-3 text-[13.5px] text-cream">{t('lab.lockedBanner')}</div>}
    </>
  );

  const sidebar = (
    <Sidebar classes={classes} activeLabId={activeLabId} onSelectLab={(id) => selectLab(id)} onJoin={() => setModal('join')}
      sessions={sessions} activeSessionId={activeSessionId} onSelectSession={(id) => { setNavOpen(false); loadSession(id); }}
      onNew={newChat} onRename={renameSession} onPin={pinSession} onExport={exportSession} onDelete={deleteSession}
      usage={usage} username={username} onSettings={() => setModal({ settings: 'general' })}
      onOpenUsage={() => setModal({ settings: 'usage' })}
      onHelp={() => setTourOpen(true)} onLogout={() => { logout(); navigate('/login'); }}
      onSearch={() => setModal('search')} onCollapse={isWide ? () => setSidebarHidden(true) : () => setNavOpen(false)} />
  );

  return (
    <div className="flex h-dvh overflow-hidden bg-ink-deep text-cream">
      {/* Sidebar: a column on desktop, a drawer on phones. */}
      {isWide ? (!sidebarHidden && <div className="w-[264px] shrink-0 border-r border-border-subtle">{sidebar}</div>) : navOpen && (
        <>
          <div className="fixed inset-0 z-30 bg-black/40" onClick={() => setNavOpen(false)} />
          <div className="fixed inset-y-0 left-0 z-40 w-[85vw] max-w-[300px] shadow-elevated">{sidebar}</div>
        </>
      )}

      <main className="relative flex min-w-0 flex-1 flex-col"
        onDragOver={(e) => { if (e.dataTransfer?.types?.includes('Files') && features.upload && !examMode) { e.preventDefault(); setDragging(true); } }}
        onDragLeave={(e) => { if (e.currentTarget === e.target) setDragging(false); }}
        onDrop={(e) => { e.preventDefault(); setDragging(false); composerRef.current?.addFiles(e.dataTransfer.files || []); }}>
        <header className="flex h-12 shrink-0 items-center gap-2 px-3">
          {(!isWide || sidebarHidden) && (
            <button type="button" onClick={() => (isWide ? setSidebarHidden(false) : setNavOpen(true))} aria-label={t('sidebar.open')}
              className="rounded-lg p-1.5 text-cream-muted hover:bg-ink-hover hover:text-cream">
              <Icon name={isWide ? 'sidebar' : 'menu'} className="h-5 w-5" />
            </button>
          )}
          {(!isWide || sidebarHidden) && (
            <button type="button" onClick={newChat} aria-label={t('chat.new')} disabled={!activeLabId}
              className="rounded-lg p-1.5 text-cream-muted hover:bg-ink-hover hover:text-cream disabled:opacity-40">
              <Icon name="newChat" className="h-5 w-5" />
            </button>
          )}
          <h1 className="min-w-0 flex-1 truncate text-[14px] text-cream-secondary">
            {sessions.find((s) => s.id === activeSessionId)?.title || activeLab?.name || ''}
          </h1>
        </header>
        {examMode && (
          <div role="status" className="shrink-0 bg-warn-soft px-4 py-2 text-center text-[13px] text-cream">
            {activeLab?.exam_files ? t('exam.bannerFiles', { n: activeLab.exam_files }) : t('exam.bannerNoFiles')}
          </div>
        )}

        {welcome ? (
          <div className="flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto px-4">
            <div className="w-full max-w-[760px]">
              {!classes.length ? (
                <div className="text-center">
                  <Icon name="book" className="mx-auto mb-3 h-8 w-8 text-cream-muted" />
                  <p className="mb-4 text-[16px] text-cream">{t('join.empty')}</p>
                  <button type="button" onClick={() => setModal('join')} className="rounded-full bg-ink px-4 py-2 text-[14px] text-on-ink">{t('join.enterCode')}</button>
                </div>
              ) : (
                <>
                  <h2 className="mb-2 text-center text-[26px] font-semibold text-cream">
                    {activeLab ? t('welcome.title', { name: username || '', lab: activeLab.name }) : t('picker.choose')}
                  </h2>
                  {activeLab && <p className="mb-6 text-center text-[13.5px] text-cream-muted">{activeClass.name} · {activeLab.name}</p>}
                  {banners}
                  {composer}
                  {activeLab && !disabledReason && (
                    <div className="mt-4 space-y-0.5">
                      {['welcome.s1', 'welcome.s2', 'welcome.s3', 'welcome.s4'].map((key) => (
                        <button key={key} type="button" onClick={() => send(t(key))}
                          className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-[14.5px] text-cream-secondary hover:bg-ink-hover hover:text-cream">
                          <Icon name="sparkles" className="h-4 w-4 shrink-0 text-cream-muted" />{t(key)}
                        </button>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        ) : (
          <>
            <div ref={listRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
              <div className="mx-auto w-full max-w-[760px] space-y-8 px-4 pb-8 pt-4">
                {loadingMessages && <div className="mx-auto h-5 w-5 animate-spin rounded-full border-2 border-cream-faint border-t-cream" />}
                <Thread chat={chat} sessionId={activeSessionId} features={features} examMode={examMode} pythonOn={tools.includes('code')}
                  disabledReason={disabledReason} onOpenSource={openSource} inputRef={inputRef}
                  hidden={hidden} onNewChat={newChat} />
              </div>
            </div>
            <div className="mx-auto w-full max-w-[760px] shrink-0 px-4">
              {banners}
              {composer}
            </div>
          </>
        )}

        {dragging && (
          <div className="pointer-events-none absolute inset-3 z-20 flex items-center justify-center rounded-3xl border-2 border-dashed border-cream-muted bg-ink-deep/80 text-[15px] text-cream">
            {t('drop.here')}
          </div>
        )}
      </main>

      {preview && preview.sessionId === activeSessionId && <SourcePanel source={preview} onClose={() => setPreview(null)} />}

      {modal === 'join' && <JoinModal onClose={() => setModal(null)} onJoined={() => { setModal(null); loadClasses(); }} />}
      {modal === 'search' && <SearchModal onClose={() => setModal(null)}
        onOpen={(hit) => { setModal(null); if (hit.lab_id && hit.lab_id !== activeLabId) selectLab(hit.lab_id).then(() => loadSession(hit.session_id)); else loadSession(hit.session_id); }} />}
      {modal?.settings && <SettingsModal tab={modal.settings} usage={usage} classes={classes} onClose={() => setModal(null)}
        onAskReset={askForReset} resetAsked={resetAsked} />}
      {tourOpen && <Tour steps={studentTour(t)} buttons={tourButtons(t)} onNav={(open) => { if (!isWide) setNavOpen(open); }}
        onClose={() => { setTourOpen(false); if (!tourSeen) markTourSeen(); }} />}
    </div>
  );
}
