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
import { fetchEventSource } from '@microsoft/fetch-event-source';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import useAuthStore from '../../store/authStore';
import { useT } from '../../lib/i18n';
import { createMessageId, createRequestId } from '../../lib/messageId';
import { interpretStreamEvent, openStream } from '../../lib/streamEvents';
import { applyToMessage } from '../../lib/streamMessage';
import { nearLimit, resetNotice } from '../../lib/quota';
import { useChatScroll } from '../../lib/chatScroll';
import { buildTurns } from '../../lib/turns';
import { runForAgent } from '../../lib/python';
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
import { Answer, Question, VersionSwitch, preloadContent } from './Turns';

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
  const { token, username, tourSeen, markTourSeen, logout } = useAuthStore();
  const isWide = useMediaQuery('(min-width: 900px)');

  const [classes, setClasses] = useState([]);
  const [activeLabId, setActiveLabId] = useState(urlLabId || null);
  const [sessions, setSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [usage, setUsage] = useState({ used: 0, limit: 0, week_used: 0, week_limit: 0, blocked: null });
  const [resetAsked, setResetAsked] = useState(false);
  const [input, setInput] = useState('');
  const [tools, setTools] = useState([]);
  const [streaming, setStreaming] = useState(false);
  const [preview, setPreview] = useState(null);
  const [navOpen, setNavOpen] = useState(false);
  const [sidebarHidden, setSidebarHidden] = useState(false);
  const [modal, setModal] = useState(null);       // null | 'join' | 'search' | {settings: tab}
  const [tourOpen, setTourOpen] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [chosenVersion, setChosenVersion] = useState({});
  const [historyStart, setHistoryStart] = useState(null);
  const [seenReset, setSeenReset] = useState(0);
  const abortRef = useRef(null);
  const listRef = useRef(null);
  const inputRef = useRef(null);
  const composerRef = useRef(null);
  const restoredRef = useRef(false);
  const streamedIds = useRef(new Set());
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

  // ── Lab and sessions ──
  const loadSession = useCallback(async (sessionId) => {
    abortRef.current?.abort();
    setStreaming(false);
    setActiveSessionId(sessionId);
    setPreview(null);
    setChosenVersion({});
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
  }, [follow, t]);

  const selectLab = useCallback(async (labId, { openLatest = false } = {}) => {
    restoredRef.current = true;
    abortRef.current?.abort();
    setActiveLabId(labId);
    setMessages([]);
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
  }, [navigate, loadSession]);

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
    abortRef.current?.abort();
    setActiveSessionId(null);
    setMessages([]);
    setPreview(null);
    setNavOpen(false);
    setTimeout(() => inputRef.current?.focus(), 0);
  }, []);

  useEffect(() => { updateScroll(); }, [messages, updateScroll]);
  useEffect(() => () => abortRef.current?.abort(), []);

  // Where the history the assistant sees starts (older messages are summarized).
  useEffect(() => {
    if (!activeSessionId || streaming) return undefined;
    let current = true;
    api.get(`/api/chat/sessions/${activeSessionId}/history-start?mode=research`)
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

  // ── Send ──
  const send = useCallback(async (text, attachments = [], { versionOf = null, toolsOverride = null } = {}) => {
    const message = (text || '').trim();
    if ((!message && !attachments.length) || streaming || !activeLabId) return;
    let sessionId = activeSessionId;
    if (!sessionId) {
      try {
        const created = await api.post(`/api/student/labs/${activeLabId}/sessions`, {});
        setSessions((prev) => [created, ...prev]);
        setActiveSessionId(created.id);
        sessionId = created.id;
      } catch (err) {
        toast.error(err.message || t('error.newChat'));
        return;
      }
    }
    const userId = createMessageId();
    const answerId = createMessageId();
    streamedIds.current.add(answerId);
    const now = new Date().toISOString();
    const root = versionOf ? (messages.find((m) => m.id === versionOf)?.version_of || versionOf) : null;
    setMessages((prev) => {
      let next = prev;
      if (versionOf) {
        // The replaced question and what follows it stay, marked replaced.
        const at = prev.findIndex((m) => m.id === versionOf);
        next = prev.map((m, i) => (i >= at && !m.superseded_at ? { ...m, superseded_at: now } : m));
      }
      return [...next,
        { id: userId, sender: 'user', content: message, attachments, version_of: root, created_at: now },
        { id: answerId, sender: 'llm', content: '', created_at: now, started_at: Date.now(), phases: [], steps: [] }];
    });
    if (versionOf) setChosenVersion((v) => ({ ...v, [root]: undefined }));
    follow('pin');
    setStreaming(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const update = (action) => setMessages((prev) => prev.map((m) => (m.id === answerId ? applyToMessage(m, action) : m)));
    let realAnswerId = answerId;
    try {
      await fetchEventSource('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          session_id: sessionId, message, client_request_id: createRequestId(), mode: 'research',
          tools: toolsOverride || tools, attachments: attachments.map((a) => ({ kind: a.kind, id: a.id })),
          version_of: versionOf || undefined,
        }),
        signal: ctrl.signal,
        openWhenHidden: true,
        onopen: openStream,
        onmessage(ev) {
          const action = interpretStreamEvent(ev);
          switch (action.type) {
            case 'status':
            case 'step':
            case 'tasks':
            case 'draft':
            case 'draft_reset':
            case 'token':
            case 'citations':
            case 'clarification':
              update(action);
              if (action.type === 'status' && activeClass && action.examMode !== !!activeClass.exam_mode) loadClasses();
              return;
            case 'code':
              update(action);
              // Python for this browser: run it, send the output back to the agent.
              runForAgent(action.id, action.code).then((output) => update({ type: 'code_output', id: action.id,
                output: { status: output.error || output.stderr ? 'error' : 'ok', ...output } }));
              return;
            case 'done':
              streamedIds.current.delete(answerId);
              realAnswerId = action.messageId || answerId;
              setMessages((prev) => prev.map((m) => {
                if (m.id === answerId) return { ...applyToMessage(m, { ...action, at: Date.now() }), id: realAnswerId };
                if (m.id === userId && action.userMessageId) return { ...m, id: action.userMessageId };
                return m;
              }));
              setStreaming(false);
              loadUsage();
              return;
            case 'title':
              setSessions((prev) => prev.map((s) => (s.id === (action.sessionId || sessionId) ? { ...s, title: action.title } : s)));
              return;
            case 'follow_ups':
              setMessages((prev) => prev.map((m) => (m.id === (action.messageId || realAnswerId) ? applyToMessage(m, action) : m)));
              return;
            case 'error':
              setStreaming(false);
              update({ type: 'draft_reset' });
              toast.error(action.detail);
              return;
            default:
          }
        },
        onerror(err) { throw err; },
      });
    } catch (err) {
      if (err?.refusal) {
        // Nothing was asked: the question goes back into the box.
        streamedIds.current.delete(answerId);
        setMessages((prev) => prev.filter((m) => m.id !== userId && m.id !== answerId)
          .map((m) => (versionOf && m.superseded_at === now ? { ...m, superseded_at: null } : m)));
        if (!versionOf) setInput((current) => current || message);
        toast.error(err.refusal.message, { duration: 8000 });
        if (err.refusal.reload === 'classes') loadClasses();
        if (err.refusal.reload === 'usage') loadUsage();
      } else if (!ctrl.signal.aborted) toast.error(t('error.stream'));
    } finally {
      setMessages((prev) => prev.map((m) => (m.id === answerId || m.id === realAnswerId
        ? { ...m, draft: '', finished_at: m.finished_at || Date.now() } : m)));
      setStreaming(false);
    }
  }, [streaming, activeLabId, activeSessionId, messages, tools, token, follow, activeClass, loadClasses, loadUsage, t]);

  const stop = () => { abortRef.current?.abort(); setStreaming(false); };

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
  const turns = useMemo(() => buildTurns(messages), [messages]);
  const lastTurn = turns[turns.length - 1];
  const locked = activeLab && !activeLab.is_active;
  const blocked = usage.blocked;
  const disabledReason = !activeLabId ? 'noLab' : locked ? 'locked' : blocked ? 'quota' : null;
  const resetKey = resetNotice(usage);
  const showReset = (() => { try { return resetKey && localStorage.getItem(resetKey) !== '1'; } catch { return false; } })();
  void seenReset;
  const hidden = historyStart?.sessionId === activeSessionId ? historyStart : null;
  const lastQuestion = lastTurn?.versions[lastTurn.current]?.question;
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
            {activeLab?.exam_files ? t('exam.bannerFiles', { n: activeLab.exam_files }) : t('exam.bannerDirect')}
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
                {turns.map((turn, ti) => {
                  const index = chosenVersion[turn.root] ?? turn.current;
                  const { question, answer } = turn.versions[index] || turn.versions[turn.current];
                  const isLast = ti === turns.length - 1;
                  const live = streaming && isLast && answer && !answer.finished_at;
                  const showLine = hidden?.hidden > 0 && question?.id === hidden.seen_from;
                  return (
                    <div key={turn.root} className="space-y-4">
                      {showLine && (
                        <div className="flex items-center gap-3 text-[12.5px] text-cream-muted">
                          <span className="h-px flex-1 bg-border-default" />
                          <span>{t('history.summarized', { n: hidden.hidden })}</span>
                          <button type="button" onClick={newChat} className="underline hover:text-cream">{t('chat.new')}</button>
                          <span className="h-px flex-1 bg-border-default" />
                        </div>
                      )}
                      {question && (
                        <Question question={question} canEdit={isLast && !streaming && !disabledReason}
                          onEdit={(text) => send(text, question.attachments || [], { versionOf: turn.versions[turn.current].question.id })}
                          versions={<VersionSwitch index={index} count={turn.versions.length}
                            onChange={(i) => setChosenVersion((v) => ({ ...v, [turn.root]: i }))} />} />
                      )}
                      {answer && (
                        <div data-latest-answer={isLast ? '' : undefined}>
                          <Answer answer={answer} live={live} isLast={isLast && index === turn.current}
                            sessionId={activeSessionId} onOpenSource={setPreview}
                            canRunCode={!!features.code && !examMode}
                            rateable={!streamedIds.current.has(answer.id)}
                            onPick={isLast && !streaming ? (option) => send(option) : null}
                            onType={() => inputRef.current?.focus()}
                            onRegenerate={!disabledReason && !streaming && lastQuestion ? () => send(lastQuestion.content, lastQuestion.attachments || [], { versionOf: lastQuestion.id }) : null}
                            onSearchCourse={!disabledReason && !streaming ? () => send(t('ask.searchCourse')) : null}
                            onDeep={!disabledReason && !streaming && features.deep && !examMode && lastQuestion
                              ? () => send(lastQuestion.content, lastQuestion.attachments || [], { versionOf: lastQuestion.id, toolsOverride: ['deep'] }) : null}
                            onAskAgain={!disabledReason && !streaming && lastQuestion ? () => send(lastQuestion.content, lastQuestion.attachments || [], { versionOf: lastQuestion.id }) : null}
                            onFollowUp={(text) => send(text)} />
                        </div>
                      )}
                    </div>
                  );
                })}
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
