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
import { interpretStreamEvent } from '../lib/streamEvents';
import { applyToMessage } from '../lib/streamMessage';
import HierarchicalSidebar from '../components/HierarchicalSidebar';
import { PdfPreviewModal } from '../components/PdfPreview';
import { MessageBubble, loadMessageContent } from '../components/ChatMessage';
import ModeSwitch from '../components/ModeSwitch';
import useMediaQuery from '../lib/useMediaQuery';
import { DEFAULT_MODE, offersResearch, saveMode, storedMode } from '../lib/answerModes';
import { useChatScroll } from '../lib/chatScroll';
// The markdown/highlight/KaTeX stack is code-split (ChatMessage loads it
// lazily). We warm that chunk on mount (see effect below) so the first
// assistant message renders as real markdown instead of the raw-text
// Suspense fallback ("squished markdown") while the large chunk downloads.

export default function Chat() {
  const { labId: urlLabId } = useParams();
  const navigate = useNavigate();
  const { token, username } = useAuthStore();

  // ── Hierarchical state ──
  const [classes, setClasses] = useState([]);
  const [classesLoading, setClassesLoading] = useState(true);

  // ── Active context ──
  const [activeLabId, setActiveLabId] = useState(urlLabId || null);
  const [sessions, setSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [usage, setUsage] = useState({ used: 0, limit: 50000 });

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
  const loadClasses = useCallback(async () => {
    try {
      setClassesLoading(true);
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
      toast.error('Failed to load classes');
    } finally {
      setClassesLoading(false);
    }
  }, []);

  useEffect(() => { loadClasses(); }, [loadClasses]);

  // Warm the markdown/highlight/KaTeX chunk immediately so the first message
  // doesn't fall back to raw-text rendering while it downloads.
  useEffect(() => { loadMessageContent(); }, []);

  // ── Load usage stats ──
  const loadUsage = useCallback(async () => {
    try {
      const data = await api.get('/api/student/usage');
      setUsage(data);
    } catch {
      // The quota bar keeps its last value.
    }
  }, []);

  useEffect(() => { loadUsage(); }, [loadUsage]);

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
    } catch {
      toast.error('Failed to load messages');
    } finally {
      setIsLoadingMessages(false);
    }
  }, [follow]);

  // ── Scroll while an answer is written (lib/chatScroll.js) ──
  useEffect(() => { updateScroll(); }, [messages, updateScroll]);

  // ── Abort the SSE stream on unmount (no leaked EventSource / setState) ──
  useEffect(() => () => abortRef.current?.abort(), []);

  // ── Send message ──
  // text: an option the student picked under a question back; otherwise the input box.
  // picked: an option of a question back, or a question sent again;
  // modeOverride: that question's mode (Research again after an empty Search).
  const handleSend = useCallback(async (picked, modeOverride) => {
    const text = (typeof picked === 'string' ? picked : input).trim();
    if (!text || !activeSessionId || isStreaming) return;

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
    // [v8.0] Client temp ids aren't the server Message ids, so this bubble can't
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
          mode: examMode ? 'direct' : (typeof modeOverride === 'string' ? modeOverride : mode) }),
        signal: ctrl.signal,
        // Keep the stream open while the tab is hidden; by default the library
        // closes it and resends the question when the tab is shown again.
        openWhenHidden: true,
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
              }
              return;
            case 'done':
              if (action.messageId) {
                streamedIdsRef.current.delete(assistantMsgId);
                setMessages((prev) => prev.map((m) => m.id === assistantMsgId
                  ? { ...m, id: action.messageId } : m));
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
    } catch {
      if (!ctrl.signal.aborted) toast.error('Stream interrupted');
    } finally {
      // An unchecked draft never stays on screen once the stream ends.
      setMessages((prev) => prev.map((m) => m.id === assistantMsgId && m.draft ? { ...m, draft: '' } : m));
      setResearchPhase(null);
      setIsStreaming(false);
    }
  }, [input, activeSessionId, activeLabId, isStreaming, token, loadUsage, mode, examMode, activeClass, follow]);

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
  const quotaPct = usage.limit > 0 ? Math.min((usage.used / usage.limit) * 100, 100) : 0;
  const activeSession = sessions.find((s) => s.id === activeSessionId);

  // Desktop: its own column next to the classes. Phones: inside the drawer.
  const sessionsPanel = (
    <>
      <div className="px-3 pt-4 pb-2 border-b border-border-subtle">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-cream-muted mb-2">Sessions</p>
        <button
          id="new-session-btn"
          onClick={handleNewSession}
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
                onBlur={() => handleRenameSession(s.id)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleRenameSession(s.id); if (e.key === 'Escape') setRenamingSessionId(null); }}
                className="w-full px-3 py-2 text-xs bg-ink-hover text-cream border-none outline-none"
              />
            ) : confirmingDeleteId === s.id ? (
              <div className="flex items-center gap-2 px-3 py-2 text-xs bg-danger-deep">
                <span className="flex-1 truncate text-danger">Delete this chat?</span>
                <button
                  onClick={() => handleDeleteSession(s.id)}
                  className="w-7 h-7 rounded flex items-center justify-center text-danger hover:text-danger-hover hover:bg-ink-hover transition-colors cursor-pointer"
                  title="Confirm delete"
                >
                  <CheckIcon className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => setConfirmingDeleteId(null)}
                  className="w-7 h-7 rounded flex items-center justify-center text-cream-muted hover:text-cream hover:bg-ink-hover transition-colors cursor-pointer"
                  title="Cancel"
                >
                  <XIcon className="w-3.5 h-3.5" />
                </button>
              </div>
            ) : (
              <>
                <button
                  onClick={() => handleSelectSession(s.id)}
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
                    <PencilIcon className="w-3.5 h-3.5" />
                  </button>
                  <button
                    onClick={() => setConfirmingDeleteId(s.id)}
                    className="w-7 h-7 rounded flex items-center justify-center text-cream-muted hover:text-danger hover:bg-ink-hover transition-colors cursor-pointer"
                    title="Delete session"
                  >
                    <TrashIcon className="w-3.5 h-3.5" />
                  </button>
                </div>
              </>
            )}
          </div>
        ))}
      </div>
    </>
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
        >
          {/* Phones: the sessions sit under the classes, above the account footer. */}
          {activeLabId && !isWide && (
            <div className="flex-[1.5] min-h-0 flex flex-col border-t border-border-subtle">
              {sessionsPanel}
            </div>
          )}
        </HierarchicalSidebar>

        {/* Token quota at bottom */}
        <div className="shrink-0 px-4 py-3 border-t border-border-subtle">
          <div className="flex justify-between text-[10px] text-cream-muted mb-1.5">
            <span>Token Quota</span>
            <span className="font-mono">{usage.used.toLocaleString()} / {usage.limit.toLocaleString()}</span>
          </div>
          <div className="h-1 bg-ink-surface rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                quotaPct > 85 ? 'bg-danger' : quotaPct > 65 ? 'bg-gold' : 'bg-cyan'
              }`}
              style={{ width: `${quotaPct}%` }}
            />
          </div>
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
              <MenuIcon className="w-5 h-5" />
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
            <div key={msg.id} data-latest-answer={i === messages.length - 1 && msg.sender === 'llm' ? '' : undefined}>
            <MessageBubble
              message={msg}
              streaming={isStreaming && msg.sender === 'llm' && i === messages.length - 1}
              phase={isStreaming && msg.sender === 'llm' && i === messages.length - 1 ? researchPhase : null}
              sessionId={activeSessionId}
              onOpenSource={openSource}
              canRate={msg.sender === 'llm' && !!msg.content && !streamedIdsRef.current.has(msg.id)}
              // Only the latest question back can still be answered with a click.
              onPick={!isStreaming && i === messages.length - 1 && msg.clarification ? handleSend : null}
              // An uncited Search answer offers the same question in Research.
              onResearch={!isStreaming && !examMode && i === messages.length - 1 && offersResearch(msg)
                ? () => {
                  const question = messages.slice(0, i).reverse().find((m) => m.sender === 'user');
                  if (question) handleSend(question.content, 'research');
                } : null}
            />
            </div>
          ))}
        </div>

        {/* Input */}
        <div className="shrink-0 px-3 py-3 md:px-6 md:py-4 border-t border-border-subtle bg-ink-base/80 backdrop-blur-sm">
          {activeSessionId && (
            <div className="mb-2">
              <ModeSwitch mode={mode} onChange={chooseMode} examMode={examMode} disabled={isStreaming} />
            </div>
          )}
          <div className={`flex items-end gap-3 p-3 rounded-xl border transition-colors ${
            activeSessionId ? 'border-border-default focus-within:border-cyan/40' : 'border-border-subtle opacity-50'
          } bg-ink-deep`}>
            <textarea
              ref={inputRef}
              id="chat-input"
              rows={1}
              disabled={!activeSessionId || isStreaming}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={activeSessionId ? 'Type your message… (Enter to send)' : 'Select a session first'}
              className="flex-1 bg-transparent text-cream text-sm resize-none outline-none placeholder:text-cream-muted max-h-32 min-h-[1.5rem] leading-relaxed"
              style={{ height: 'auto' }}
            />
            <button
              id="send-btn"
              onClick={isStreaming ? () => abortRef.current?.abort() : handleSend}
              disabled={!activeSessionId}
              className={`shrink-0 p-2 rounded-lg transition-all cursor-pointer ${
                isStreaming
                  ? 'bg-danger-muted text-danger hover:bg-danger/20'
                  : 'gradient-cyan text-on-accent hover:brightness-110 shadow-glow disabled:opacity-40 disabled:cursor-not-allowed'
              }`}
            >
              {isStreaming ? <StopIcon className="w-4 h-4" /> : <SendIcon className="w-4 h-4" />}
            </button>
          </div>
          {/* [v8.0] Transparency: students are told their teacher can see the chat. */}
          <p className="mt-2 text-center text-[11px] text-cream-muted">
            Your teacher can see this conversation.
          </p>
        </div>
      </div>

      {preview && preview.sessionId === activeSessionId && (
        <PdfPreviewModal source={preview} onClose={closeSource} />
      )}

      {/* ── Join Class Modal ── */}
      {showJoinModal && (
        <div className="fixed inset-0 bg-ink-deep/80 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-fade-in">
          <div className="bg-ink-base rounded-2xl border border-border-default shadow-elevated max-w-sm w-full p-8 noise">
            <h3 className="font-display text-2xl text-cream mb-1">Join a Class</h3>
            <p className="text-sm text-cream-secondary mb-6">Enter the 6-character invite code from your teacher.</p>

            <div className="flex gap-2 justify-center mb-6">
              {joinCode.map((ch, i) => (
                <input
                  key={i}
                  id={`jc-${i}`}
                  type="text"
                  maxLength={1}
                  value={ch}
                  onChange={(e) => handleJoinCodeChange(i, e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Backspace' && !ch && i > 0) document.getElementById(`jc-${i - 1}`)?.focus();
                  }}
                  className="w-11 h-13 text-center text-lg font-mono font-bold text-cream bg-ink-deep border border-border-default rounded-lg focus:border-cyan focus:ring-1 focus:ring-cyan/30 outline-none transition-colors uppercase"
                />
              ))}
            </div>

            <div className="flex gap-3">
              <button onClick={() => { setShowJoinModal(false); setJoinCode(['', '', '', '', '', '']); }}
                className="flex-1 py-2.5 rounded-lg border border-border-default text-cream-secondary text-sm hover:bg-ink-hover transition-all cursor-pointer">
                Cancel
              </button>
              <button
                onClick={handleJoinSubmit}
                disabled={isJoining || joinCode.join('').length !== 6}
                className="flex-1 py-2.5 rounded-lg gradient-cyan text-on-accent text-sm font-semibold disabled:opacity-50 cursor-pointer hover:brightness-110 transition-all">
                {isJoining ? 'Joining…' : 'Join Class'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Icons ── */
function MenuIcon({ className }) {
  return <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M4 6h16M4 12h16M4 18h16" /></svg>;
}
function SendIcon({ className }) {
  return <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m22 2-7 20-4-9-9-4 20-7z" /><path d="M22 2 11 13" /></svg>;
}
function StopIcon({ className }) {
  return <svg className={className} viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>;
}
function PencilIcon({ className }) {
  return <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" /></svg>;
}
function TrashIcon({ className }) {
  return <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>;
}
function CheckIcon({ className }) {
  return <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5" /></svg>;
}
function XIcon({ className }) {
  return <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6 6 18" /><path d="m6 6 12 12" /></svg>;
}
