/**
 * Chat.jsx — Student unified workspace for Edu-LLM.
 *
 * Layout: Left hierarchical sidebar (Class→Lab tree) + Right chat panel.
 * Students pick a lab from the sidebar; sessions are created automatically.
 * Deleting a session is a SOFT delete (DELETE /api/student/sessions/{id}): it
 * disappears from the student's list but is retained and stays visible to
 * teacher/admin audit. Only admin can hard-delete.
 *
 * Join class flow is a modal triggered from the sidebar.
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import toast from 'react-hot-toast';
import useAuthStore from '../store/authStore';
import api from '../lib/api';
import { createMessageId, createRequestId } from '../lib/messageId';
import { interpretStreamEvent, openStream } from '../lib/streamEvents';
import { applyToMessage } from '../lib/streamMessage';
import HierarchicalSidebar from '../components/HierarchicalSidebar';
import { PdfPreviewModal } from '../components/PdfPreview';
import { MessageBubble, loadMessageContent } from '../components/ChatMessage';
import SessionsPanel from './chat/SessionsPanel';
import MessageInput from './chat/MessageInput';
import JoinClassModal from './chat/JoinClassModal';
import HistoryLine from './chat/HistoryLine';
import QuotaBar from './chat/QuotaBar';
import Icon from '../components/Icon';
import Tour from '../components/Tour';
import { TOURS } from '../lib/tours';
import useMediaQuery from '../lib/useMediaQuery';
import { DEFAULT_MODE, offersResearch, saveMode, sessionMode, storedMode } from '../lib/answerModes';
import { useChatScroll } from '../lib/chatScroll';
import { historyLineIndex, messageLength } from '../lib/historyLine';
import { blockedMessage, nearLimit, nearLimitMessage, resetNotice } from '../lib/quota';
// The markdown/highlight/KaTeX stack is code-split (ChatMessage loads it
// lazily). We warm that chunk on mount (see effect below) so the first
// assistant message renders as real markdown instead of the raw-text
// Suspense fallback ("squished markdown") while the large chunk downloads.

// The class list (and with it exam mode and locked labs) is loaded again this
// often while the page is shown, and when the student comes back to the tab.
const CLASS_REFRESH_MS = 90_000;
const MIN_REFRESH_GAP_MS = 10_000;

export default function Chat() {
  const { labId: urlLabId } = useParams();
  const navigate = useNavigate();
  const { token, username, tourSeen, markTourSeen } = useAuthStore();
  // The guided tour starts by itself until it has been finished or skipped once.
  const [tourOpen, setTourOpen] = useState(false);
  useEffect(() => { if (!tourSeen) setTourOpen(true); }, [tourSeen]);

  // ── Hierarchical state ──
  const [classes, setClasses] = useState([]);
  const [classesLoading, setClassesLoading] = useState(true);

  // ── Active context ──
  const [activeLabId, setActiveLabId] = useState(urlLabId || null);
  const [sessions, setSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  // Day and week limits (lib/quota.js); a started question always finishes.
  const [usage, setUsage] = useState({ used: 0, limit: 0, week_used: 0, week_limit: 0, blocked: null });
  const [askingReset, setAskingReset] = useState(false);

  // ── Chat state ──
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [researchPhase, setResearchPhase] = useState(null);
  // The cited page shown in the preview window over the chat (null = closed).
  const [preview, setPreview] = useState(null);
  const openSource = useCallback((source) => setPreview(source), []);
  const closeSource = useCallback(() => setPreview(null), []);
  const restoredLabRef = useRef(false);
  const [isLoadingMessages, setIsLoadingMessages] = useState(false);
  // Phones: classes and sessions live in a drawer opened from the header.
  const [navOpen, setNavOpen] = useState(!urlLabId);
  const isWide = useMediaQuery('(min-width: 768px)');
  // Answer mode of the next question (Direct / Search / Research), per session.
  const [mode, setMode] = useState(DEFAULT_MODE);
  useEffect(() => { setMode(storedMode(activeSessionId)); }, [activeSessionId]);
  const chooseMode = (next) => { setMode(next); saveMode(activeSessionId, next); };
  // Exam mode (set by the teacher for the class): answers without course search.
  const activeClass = classes.find((c) => c.labs?.some((l) => l.id === activeLabId));
  const examMode = !!activeClass?.exam_mode;
  // In exam mode, the files of this lab the teacher allowed (0: Direct only).
  const examFiles = activeClass?.labs?.find((l) => l.id === activeLabId)?.exam_files || 0;
  const directOnly = examMode && !examFiles;
  const abortRef = useRef(null);
  const listRef = useRef(null);
  const { onScroll, update: updateScroll, follow } = useChatScroll(listRef);
  const inputRef = useRef(null);
  const streamedIdsRef = useRef(new Set()); // temp ids not yet persisted server-side

  // ── Join modal state ──
  const [showJoinModal, setShowJoinModal] = useState(false);
  const [joinCode, setJoinCode] = useState(['', '', '', '', '', '']);
  const [isJoining, setIsJoining] = useState(false);

  // ── Rename session state ──
  const [renamingSessionId, setRenamingSessionId] = useState(null);
  const [renameTitle, setRenameTitle] = useState('');

  // ── Soft-delete confirm state (inline, mirrors the rename pattern) ──
  const [confirmingDeleteId, setConfirmingDeleteId] = useState(null);

  // ── Load classes with labs ──
  // quiet: a background refresh, without the loading state or an error toast.
  const loadClasses = useCallback(async ({ quiet = false } = {}) => {
    try {
      if (!quiet) setClassesLoading(true);
      const classData = await api.get('/api/student/classes');
      // For each class, fetch its labs
      const withLabs = await Promise.all(
        classData.map(async (cls) => {
          const labs = await api.get(`/api/student/classes/${cls.id}/labs`).catch(() => []);
          return { ...cls, labs };
        })
      );
      setClasses(withLabs);
    } catch {
      if (!quiet) toast.error('Failed to load classes');
    } finally {
      if (!quiet) setClassesLoading(false);
    }
  }, []);

  useEffect(() => { loadClasses(); }, [loadClasses]);

  // The teacher may turn exam mode on or off, or lock a lab, while this page is
  // open: load the classes again now and then, and when the student comes back.
  useEffect(() => {
    let last = Date.now();
    const refresh = () => {
      if (document.visibilityState !== 'visible' || Date.now() - last < MIN_REFRESH_GAP_MS) return;
      last = Date.now();
      loadClasses({ quiet: true });
    };
    const id = setInterval(refresh, CLASS_REFRESH_MS);
    document.addEventListener('visibilitychange', refresh);
    window.addEventListener('focus', refresh);
    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', refresh);
      window.removeEventListener('focus', refresh);
    };
  }, [loadClasses]);

  // Warm the markdown/highlight/KaTeX chunk immediately so the first message
  // doesn't fall back to raw-text rendering while it downloads.
  useEffect(() => { loadMessageContent(); }, []);

  // ── Load usage stats ──
  const loadUsage = useCallback(async () => {
    try {
      const data = await api.get('/api/student/usage');
      setUsage(data);
      // Told once (per day or week) when 80% of a limit is used.
      const near = nearLimit(data);
      if (near) {
        let seen = false;
        try { seen = localStorage.getItem(near.key) === '1'; localStorage.setItem(near.key, '1'); } catch { /* private window */ }
        if (!seen) toast(nearLimitMessage(near.kind), { duration: 8000 });
      }
    } catch {
      // The quota bars keep their last value.
    }
  }, []);

  useEffect(() => { loadUsage(); }, [loadUsage]);

  // A teacher may reset the limits while this page is open (same rhythm as the classes).
  useEffect(() => {
    let last = Date.now();
    const refresh = () => {
      if (document.visibilityState !== 'visible' || Date.now() - last < MIN_REFRESH_GAP_MS) return;
      last = Date.now();
      loadUsage();
    };
    const id = setInterval(refresh, CLASS_REFRESH_MS);
    document.addEventListener('visibilitychange', refresh);
    window.addEventListener('focus', refresh);
    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', refresh);
      window.removeEventListener('focus', refresh);
    };
  }, [loadUsage]);

  const askForReset = useCallback(async () => {
    setAskingReset(true);
    try {
      const data = await api.post('/api/student/usage/reset-request');
      setUsage(data);
      toast.success(data.sent ? 'Request sent to your teacher.' : 'Your teacher already has your request.');
    } catch (err) {
      toast.error(err.message);
      loadUsage();
    } finally {
      setAskingReset(false);
    }
  }, [loadUsage]);

  // ── When a lab is selected ──
  const handleSelectLab = useCallback(async (labId) => {
    restoredLabRef.current = true;
    setActiveLabId(labId);
    setMessages([]);
    setActiveSessionId(null);
    navigate(`/chat/${labId}`, { replace: true });

    // Load or create a session for this lab
    try {
      const existing = await api.get(`/api/student/sessions?lab_id=${labId}`);
      if (existing.length > 0) {
        const latest = existing[0];
        setActiveSessionId(latest.id);
        follow('tail');
        setSessions(existing);
        // Load messages
        const sessionData = await api.get(`/api/student/sessions/${latest.id}`);
        setMessages(sessionData.messages || []);
        setMode(sessionMode(latest.id, sessionData.messages));
      } else {
        setSessions([]);
      }
    } catch {
      setSessions([]);
    }
  }, [navigate, follow]);

  // The URL survives a reload; restore its latest session and persisted citations.
  useEffect(() => {
    if (urlLabId && !restoredLabRef.current) handleSelectLab(urlLabId, null);
  }, [urlLabId, handleSelectLab]);

  // ── Create new session ──
  const handleNewSession = useCallback(async () => {
    if (!activeLabId) return;
    try {
      // No title: the first question and answer give it one (server side).
      const newSession = await api.post(`/api/student/labs/${activeLabId}/sessions`, {});
      setSessions((prev) => [newSession, ...prev]);
      setActiveSessionId(newSession.id);
      setMessages([]);
      setNavOpen(false);
    } catch (err) {
      toast.error(err.message || 'Failed to create session');
    }
  }, [activeLabId]);

  // ── Load session messages ──
  const handleSelectSession = useCallback(async (sessionId) => {
    // Abort any in-flight stream so its tokens never land on the new session.
    abortRef.current?.abort();
    abortRef.current = null;
    setIsStreaming(false);
    setActiveSessionId(sessionId);
    follow('tail');
    setNavOpen(false);
    setIsLoadingMessages(true);
    try {
      const data = await api.get(`/api/student/sessions/${sessionId}`);
      setMessages(data.messages || []);
      setMode(sessionMode(sessionId, data.messages));
    } catch {
      toast.error('Failed to load messages');
    } finally {
      setIsLoadingMessages(false);
    }
  }, [follow]);

  // ── Scroll while an answer is written (lib/chatScroll.js) ──
  useEffect(() => { updateScroll(); }, [messages, updateScroll]);

  // ── Where the assistant's view of the conversation starts (lib/historyLine.js) ──
  // Asked again when the session, the mode or the conversation changes.
  const [historyStart, setHistoryStart] = useState(null);
  const reloadedForLineRef = useRef(null);
  useEffect(() => {
    if (!activeSessionId || isStreaming) return undefined;
    let current = true;
    api.get(`/api/chat/sessions/${activeSessionId}/history-start?mode=${mode}`)
      .then((start) => { if (current) setHistoryStart({ ...start, sessionId: activeSessionId }); })
      .catch(() => {});
    return () => { current = false; };
  }, [activeSessionId, mode, isStreaming]);
  const line = historyLineIndex(messages, historyStart?.sessionId === activeSessionId ? historyStart : null);
  // The line falls on a message this page knows only by a temporary id (the
  // done event normally gives the real one): load the session again, once.
  // Not while an answer is written: the reload would wipe it.
  const streamingRef = useRef(false);
  useEffect(() => { streamingRef.current = isStreaming; }, [isStreaming]);
  useEffect(() => {
    if (!line.missing || !activeSessionId || isStreaming || reloadedForLineRef.current === historyStart) return;
    reloadedForLineRef.current = historyStart;
    const sessionId = activeSessionId;
    api.get(`/api/student/sessions/${sessionId}`)
      .then((data) => { if (!streamingRef.current) setMessages(data.messages || []); })
      .catch(() => {});
  }, [line.missing, activeSessionId, isStreaming, historyStart]);
  const length = messageLength(input);
  // The last answer asks the student a question back.
  const lastMessage = messages[messages.length - 1];
  const awaitingAnswer = !isStreaming && lastMessage?.sender === 'llm' && !!lastMessage.clarification?.options?.length;

  // ── Abort the SSE stream on unmount (no leaked EventSource / setState) ──
  useEffect(() => () => abortRef.current?.abort(), []);

  // ── Send message ──
  // text: an option the student picked under a question back; otherwise the input box.
  // picked: an option of a question back, or a question sent again;
  // modeOverride: that question's mode (Research again after an empty Search).
  const handleSend = useCallback(async (picked, modeOverride) => {
    const text = (typeof picked === 'string' ? picked : input).trim();
    if (!text || !activeSessionId || isStreaming || messageLength(text).over) return;

    let currentSessionId = activeSessionId;
    // Auto-create session if none exists
    if (!currentSessionId) {
      try {
        const newSession = await api.post(`/api/student/labs/${activeLabId}/sessions`, {});
        setSessions((prev) => [newSession, ...prev]);
        setActiveSessionId(newSession.id);
        currentSessionId = newSession.id;
      } catch {
        toast.error('Could not start session');
        return;
      }
    }

    // Prefix + sequence keeps pending message IDs distinct, even in the same
    // millisecond, so streamed tokens reach the correct bubble on HTTP too.
    const userMsgId = createMessageId();
    const assistantMsgId = createMessageId();
    // Client temp ids aren't the server Message ids, so this bubble can't
    // be rated until the session reloads (feedback endpoint needs the real id).
    streamedIdsRef.current.add(assistantMsgId);
    const userMsg = { id: userMsgId, sender: 'user', content: text, created_at: new Date().toISOString() };
    setMessages((prev) => [...prev, userMsg]);
    follow('pin');
    if (typeof picked !== 'string') setInput('');
    setIsStreaming(true);

    setMessages((prev) => [...prev, { id: assistantMsgId, sender: 'llm', content: '', created_at: new Date().toISOString() }]);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      await fetchEventSource('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ session_id: currentSessionId, message: text,
          client_request_id: createRequestId(),
          // The server applies exam mode; the page may not know yet that it changed.
          mode: typeof modeOverride === 'string' ? modeOverride : mode }),
        signal: ctrl.signal,
        // Keep the stream open while the tab is hidden; by default the library
        // closes it and resends the question when the tab is shown again.
        openWhenHidden: true,
        // A question refused before the stream starts (lab locked, quota used…).
        onopen: openStream,
        onmessage(ev) {
          // Named events (status/done/error/citations) are matched before the
          // content-token branch, so the opening `status` frame is never appended
          // as text (which would render "[object Object]" atop the reply).
          const action = interpretStreamEvent(ev);
          switch (action.type) {
            case 'status':
              setResearchPhase(action.phase);
              if (action.mode) {
                setMessages((prev) => prev.map((m) => m.id === assistantMsgId ? { ...m, answer_mode: action.mode } : m));
              }
              // The teacher may have turned exam mode on or off since the page loaded.
              if (activeClass && action.examMode !== !!activeClass.exam_mode) {
                setClasses((prev) => prev.map((c) => c.id === activeClass.id ? { ...c, exam_mode: action.examMode } : c));
                loadClasses({ quiet: true });   // and which files the exam keeps open
              }
              return;
            case 'done':
              if (action.messageId) {
                streamedIdsRef.current.delete(assistantMsgId);
                setMessages((prev) => prev.map((m) => m.id === assistantMsgId
                  ? { ...m, id: action.messageId } : m));
              }
              // The question's real id, so the history line can sit above it.
              if (action.userMessageId) {
                setMessages((prev) => prev.map((m) => m.id === userMsgId ? { ...m, id: action.userMessageId } : m));
              }
              setResearchPhase(null);
              setIsStreaming(false);
              loadUsage();
              return;
            case 'title':
              setSessions((prev) => prev.map((s) => s.id === (action.sessionId || currentSessionId)
                ? { ...s, title: action.title } : s));
              return;
            case 'error':
              // Server-side mid-stream failure: stop the spinner and surface it,
              // never append the error payload as if it were model output.
              setIsStreaming(false);
              setMessages((prev) => prev.map((m) => m.id === assistantMsgId ? { ...m, draft: '' } : m));
              toast.error(action.detail);
              return;
            // Draft, answer, citations and a question back change the bubble.
            case 'draft':
            case 'draft_reset':
            case 'token':
            case 'citations':
            case 'clarification':
              setMessages((prev) => prev.map((m) => (m.id === assistantMsgId ? applyToMessage(m, action) : m)));
              return;
            default:
              return;
          }
        },
        onerror(err) { throw err; },
      });
    } catch (err) {
      if (err?.refusal) {
        // Nothing was asked: take the question back off the screen, into the box.
        streamedIdsRef.current.delete(assistantMsgId);
        setMessages((prev) => prev.filter((m) => m.id !== userMsgId && m.id !== assistantMsgId));
        if (typeof picked !== 'string') setInput((current) => current || text);
        toast.error(err.refusal.message, { duration: 8000 });
        if (err.refusal.reload === 'classes') loadClasses({ quiet: true });
        if (err.refusal.reload === 'usage') loadUsage();
      } else if (!ctrl.signal.aborted) toast.error('Stream interrupted');
    } finally {
      // An unchecked draft never stays on screen once the stream ends.
      setMessages((prev) => prev.map((m) => m.id === assistantMsgId && m.draft ? { ...m, draft: '' } : m));
      setResearchPhase(null);
      setIsStreaming(false);
    }
  }, [input, activeSessionId, activeLabId, isStreaming, token, loadUsage, loadClasses, mode, activeClass, follow]);

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
  };

  // ── Join class ──
  const handleJoinSubmit = async () => {
    const code = joinCode.join('').trim().toUpperCase();
    if (code.length !== 6) return;
    setIsJoining(true);
    try {
      const result = await api.post('/api/student/classes/join', { invite_code: code });
      toast.success(`Joined ${result.class_name}!`);
      setShowJoinModal(false);
      setJoinCode(['', '', '', '', '', '']);
      loadClasses(); // Optimistic refresh
    } catch (err) {
      toast.error(err.message || 'Invalid invite code');
    } finally {
      setIsJoining(false);
    }
  };

  const handleJoinCodeChange = (i, val) => {
    const filtered = val.replace(/[^a-zA-Z0-9]/g, '').toUpperCase().slice(0, 1);
    const next = [...joinCode];
    next[i] = filtered;
    setJoinCode(next);
    if (filtered && i < 5) document.getElementById(`jc-${i + 1}`)?.focus();
  };

  // ── Rename session ──
  const handleRenameSession = async (sessionId) => {
    if (!renameTitle.trim()) { setRenamingSessionId(null); return; }
    try {
      const updated = await api.put(`/api/student/sessions/${sessionId}`, { title: renameTitle });
      setSessions((prev) => prev.map((s) => s.id === sessionId ? { ...s, title: updated.title } : s));
      setRenamingSessionId(null);
    } catch (err) {
      toast.error(err.message);
    }
  };

  // ── Soft-delete session (hidden from student; retained for teacher/admin audit) ──
  const handleDeleteSession = useCallback(async (sessionId) => {
    try {
      await api.delete(`/api/student/sessions/${sessionId}`);
    } catch (err) {
      toast.error(err.message || 'Failed to delete session');
      return;
    } finally {
      setConfirmingDeleteId(null);
    }
    setSessions((prev) => prev.filter((s) => s.id !== sessionId));
    if (activeSessionId === sessionId) {
      setActiveSessionId(null);
      setMessages([]);
    }
    toast.success('Chat deleted');
  }, [activeSessionId]);

  // ── Derived ──
  const blockedText = blockedMessage(usage);
  // The teacher reset the limits: say so until the student closes the note.
  const resetKey = resetNotice(usage);
  const [seenTick, setSeenTick] = useState(0);
  const seenReset = (() => { try { return !resetKey || localStorage.getItem(resetKey) === '1'; } catch { return false; } })();
  const dismissReset = () => {
    try { if (resetKey) localStorage.setItem(resetKey, '1'); } catch { /* private window */ }
    setSeenTick(seenTick + 1);
  };
  const activeSession = sessions.find((s) => s.id === activeSessionId);

  // Desktop: its own column next to the classes. Phones: inside the drawer.
  const sessionsPanel = (
    <SessionsPanel
      sessions={sessions} activeSessionId={activeSessionId}
      onNewSession={handleNewSession} onSelectSession={handleSelectSession}
      renamingSessionId={renamingSessionId} setRenamingSessionId={setRenamingSessionId}
      renameTitle={renameTitle} setRenameTitle={setRenameTitle} onRenameSession={handleRenameSession}
      confirmingDeleteId={confirmingDeleteId} setConfirmingDeleteId={setConfirmingDeleteId}
      onDeleteSession={handleDeleteSession}
    />
  );

  return (
    <div className="flex h-dvh bg-ink-deep overflow-hidden">
      {/* Phones: the two sidebars below form one drawer over the chat. */}
      {navOpen && (
        <div className="fixed inset-0 z-30 bg-black/40 md:hidden" onClick={() => setNavOpen(false)} />
      )}
      <div className={`${navOpen ? 'flex' : 'hidden'} fixed inset-y-0 left-0 z-40 w-[85vw] max-w-sm flex-col shadow-elevated
        md:static md:z-auto md:flex md:w-auto md:max-w-none md:flex-row md:shadow-none`}>
      {/* ── Left: Hierarchical Sidebar ── */}
      <div className="flex-1 min-h-0 md:flex-none md:w-60 shrink-0 flex flex-col md:border-r border-border-subtle bg-ink-base">
        <HierarchicalSidebar
          role="student"
          classes={classes}
          selectedLabId={activeLabId}
          onSelectLab={handleSelectLab}
          onJoinClass={() => setShowJoinModal(true)}
          loading={classesLoading}
          onHelp={() => setTourOpen(true)}
        >
          {/* Phones: the sessions sit under the classes, above the account footer. */}
          {activeLabId && !isWide && (
            <div className="flex-[1.5] min-h-0 flex flex-col border-t border-border-subtle">
              {sessionsPanel}
            </div>
          )}
        </HierarchicalSidebar>

        {/* Token limits at bottom: today and this week */}
        <div data-tour="quota" className="shrink-0 px-4 py-3 border-t border-border-subtle space-y-2"
          title="A question that has started always finishes. Today resets at midnight, the week on Monday.">
          <QuotaBar label="Today" used={usage.used} limit={usage.limit} />
          <QuotaBar label="This week" used={usage.week_used} limit={usage.week_limit} />
        </div>
      </div>

      {/* ── Middle: Session list (desktop, when a lab is selected) ── */}
      {activeLabId && isWide && (
        <div className="w-48 shrink-0 flex flex-col border-r border-border-subtle bg-ink-base">
          {sessionsPanel}
        </div>
      )}
      </div>

      {/* ── Right: Chat panel ── */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Chat header */}
        <div className="shrink-0 h-14 px-3 md:px-6 border-b border-border-subtle flex items-center justify-between gap-2 bg-ink-base/80 backdrop-blur-sm">
          <div className="flex items-center gap-2 min-w-0">
            <button type="button" onClick={() => setNavOpen(true)} title="Classes and sessions" aria-label="Classes and sessions"
              className="md:hidden p-2 -ml-1 rounded-lg text-cream-secondary hover:text-cream hover:bg-ink-hover cursor-pointer">
              <Icon name="menu" className="w-5 h-5" />
            </button>
            {activeSession ? (
              <h2 className="text-sm font-semibold text-cream truncate">{activeSession.title || 'New conversation'}</h2>
            ) : activeLabId ? (
              <h2 className="text-sm font-semibold text-cream-secondary">Select or create a session</h2>
            ) : (
              <h2 className="text-sm font-semibold text-cream-secondary">Select a lab to begin</h2>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="hidden sm:inline text-[11px] text-cream-muted font-mono">{username}</span>
            {isStreaming && (
              <div className="flex items-center gap-1.5 text-[10px] text-cyan">
                <div className="w-1.5 h-1.5 rounded-full bg-cyan animate-pulse" />
                streaming
              </div>
            )}
          </div>
        </div>

        {/* Messages */}
        <div ref={listRef} onScroll={onScroll} className="relative flex-1 overflow-y-auto px-3 py-4 md:px-6 md:py-6 space-y-5">
          {!activeLabId && (
            <div className="flex items-center justify-center h-full">
              <div className="text-center max-w-xs">
                <div className="w-16 h-16 rounded-2xl gradient-cyan mx-auto mb-4 flex items-center justify-center shadow-glow">
                  <svg className="w-8 h-8 text-on-accent" fill="none" stroke="currentColor" strokeWidth="1.5" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M8.625 12a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 0 1-2.555-.337A5.972 5.972 0 0 1 5.41 20.97a5.969 5.969 0 0 1-.474-.065 4.48 4.48 0 0 0 .978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25Z" />
                  </svg>
                </div>
                <h3 className="font-display text-xl text-cream mb-2">Select a Lab</h3>
                <p className="text-sm text-cream-secondary">
                  Choose a lab to start chatting, or join a class first.
                </p>
              </div>
            </div>
          )}

          {activeLabId && !activeSessionId && sessions.length === 0 && (
            <div className="flex items-center justify-center h-full">
              <div className="text-center">
                <p className="text-cream-secondary text-sm mb-3">No sessions yet for this lab.</p>
                <button onClick={handleNewSession} className="px-4 py-2 rounded-lg gradient-cyan text-on-accent text-sm font-medium cursor-pointer hover:brightness-110 transition-all">
                  Start First Session
                </button>
              </div>
            </div>
          )}

          {isLoadingMessages && (
            <div className="flex items-center justify-center py-12">
              <div className="w-5 h-5 rounded-full border-2 border-cyan border-t-transparent animate-[spin_0.8s_linear_infinite]" />
            </div>
          )}

          {messages.map((msg, i) => (
            // The latest answer: scrolling stops when its first line reaches the top.
            <div key={msg.id} data-latest-answer={i === messages.length - 1 && msg.sender === 'llm' ? '' : undefined}
              className={i < line.index ? 'opacity-60' : undefined}>
            {i === line.index && <HistoryLine onNewSession={handleNewSession} />}
            <MessageBubble
              message={msg}
              streaming={isStreaming && msg.sender === 'llm' && i === messages.length - 1}
              phase={isStreaming && msg.sender === 'llm' && i === messages.length - 1 ? researchPhase : null}
              sessionId={activeSessionId}
              onOpenSource={openSource}
              canRate={msg.sender === 'llm' && !!msg.content && !streamedIdsRef.current.has(msg.id)}
              // Only the latest question back can still be answered with a click.
              onPick={!isStreaming && i === messages.length - 1 && msg.clarification ? handleSend : null}
              onType={() => { inputRef.current?.focus(); inputRef.current?.scrollIntoView({ block: 'nearest' }); }}
              // An uncited Search answer offers the same question in Research.
              onResearch={!isStreaming && !directOnly && i === messages.length - 1 && offersResearch(msg)
                ? () => {
                  const question = messages.slice(0, i).reverse().find((m) => m.sender === 'user');
                  if (question) handleSend(question.content, 'research');
                } : null}
            />
            </div>
          ))}
          {line.index === messages.length && messages.length > 0 && <HistoryLine onNewSession={handleNewSession} />}
        </div>

        {/* Input */}
        <MessageInput
          resetKey={resetKey} seenReset={seenReset} dismissReset={dismissReset}
          blockedText={blockedText} usage={usage} askForReset={askForReset} askingReset={askingReset}
          activeSessionId={activeSessionId} mode={mode} chooseMode={chooseMode}
          examMode={examMode} examFiles={examFiles} isStreaming={isStreaming}
          length={length} inputRef={inputRef} input={input} setInput={setInput}
          onKeyDown={handleKeyDown} awaitingAnswer={awaitingAnswer} abortRef={abortRef} onSend={handleSend}
        />
      </div>

      {tourOpen && (
        <Tour steps={TOURS.student} onNav={(open) => { if (!isWide) setNavOpen(open); }}
          onClose={() => { setTourOpen(false); if (!tourSeen) markTourSeen(); }} />
      )}

      {preview && preview.sessionId === activeSessionId && (
        <PdfPreviewModal source={preview} onClose={closeSource} />
      )}

      {/* ── Join Class Modal ── */}
      {showJoinModal && (
        <JoinClassModal
          joinCode={joinCode}
          isJoining={isJoining}
          onCodeChange={handleJoinCodeChange}
          onCancel={() => { setShowJoinModal(false); setJoinCode(['', '', '', '', '', '']); }}
          onSubmit={handleJoinSubmit}
        />
      )}
    </div>
  );
}
