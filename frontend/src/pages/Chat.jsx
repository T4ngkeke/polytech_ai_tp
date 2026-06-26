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
import HierarchicalSidebar from '../components/HierarchicalSidebar';

export default function Chat() {
  const { labId: urlLabId } = useParams();
  const navigate = useNavigate();
  const { token, username } = useAuthStore();

  // ── Hierarchical state ──
  const [classes, setClasses] = useState([]);
  const [classesLoading, setClassesLoading] = useState(true);

  // ── Active context ──
  const [activeLabId, setActiveLabId] = useState(urlLabId || null);
  const [activeClassId, setActiveClassId] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [usage, setUsage] = useState({ used: 0, limit: 50000 });

  // ── Chat state ──
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [isLoadingMessages, setIsLoadingMessages] = useState(false);
  const abortRef = useRef(null);
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);

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
    } catch (err) {
      toast.error('Failed to load classes');
    } finally {
      setClassesLoading(false);
    }
  }, []);

  useEffect(() => { loadClasses(); }, [loadClasses]);

  // ── Load usage stats ──
  const loadUsage = useCallback(async () => {
    try {
      const data = await api.get('/api/student/usage');
      setUsage(data);
    } catch {}
  }, []);

  useEffect(() => { loadUsage(); }, [loadUsage]);

  // ── When a lab is selected ──
  const handleSelectLab = useCallback(async (labId, classId) => {
    setActiveLabId(labId);
    setActiveClassId(classId);
    setMessages([]);
    setActiveSessionId(null);
    navigate(`/chat/${labId}`, { replace: true });

    // Load or create a session for this lab
    try {
      const existing = await api.get(`/api/student/sessions?lab_id=${labId}`);
      if (existing.length > 0) {
        const latest = existing[0];
        setActiveSessionId(latest.id);
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
  }, [navigate]);

  // ── Create new session ──
  const handleNewSession = useCallback(async () => {
    if (!activeLabId) return;
    try {
      const newSession = await api.post(`/api/student/labs/${activeLabId}/sessions`, {
        title: `Session ${new Date().toLocaleDateString()}`,
      });
      setSessions((prev) => [newSession, ...prev]);
      setActiveSessionId(newSession.id);
      setMessages([]);
    } catch (err) {
      toast.error(err.message || 'Failed to create session');
    }
  }, [activeLabId]);

  // ── Load session messages ──
  const handleSelectSession = useCallback(async (sessionId) => {
    setActiveSessionId(sessionId);
    setIsLoadingMessages(true);
    try {
      const data = await api.get(`/api/student/sessions/${sessionId}`);
      setMessages(data.messages || []);
    } catch {
      toast.error('Failed to load messages');
    } finally {
      setIsLoadingMessages(false);
    }
  }, []);

  // ── Scroll to bottom ──
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // ── Send message ──
  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || !activeSessionId || isStreaming) return;

    let currentSessionId = activeSessionId;
    // Auto-create session if none exists
    if (!currentSessionId) {
      try {
        const newSession = await api.post(`/api/student/labs/${activeLabId}/sessions`, { title: text.slice(0, 40) });
        setSessions((prev) => [newSession, ...prev]);
        setActiveSessionId(newSession.id);
        currentSessionId = newSession.id;
      } catch (err) {
        toast.error('Could not start session');
        return;
      }
    }

    const userMsg = { id: Date.now(), sender: 'user', content: text, created_at: new Date().toISOString() };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setIsStreaming(true);

    const assistantMsgId = Date.now() + 1;
    setMessages((prev) => [...prev, { id: assistantMsgId, sender: 'llm', content: '', created_at: new Date().toISOString() }]);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      await fetchEventSource('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ session_id: currentSessionId, message: text }),
        signal: ctrl.signal,
        onmessage(ev) {
          if (ev.event === 'done') { setIsStreaming(false); loadUsage(); return; }
          if (ev.event === 'citations') {
            let cites = [];
            try { cites = JSON.parse(ev.data); } catch { /* ignore malformed */ }
            setMessages((prev) => prev.map((m) =>
              m.id === assistantMsgId ? { ...m, citations: cites } : m
            ));
            return;
          }
          if (ev.data) {
            setMessages((prev) => prev.map((m) =>
              m.id === assistantMsgId ? { ...m, content: m.content + ev.data } : m
            ));
          }
        },
        onerror(err) { throw err; },
      });
    } catch (err) {
      if (!ctrl.signal.aborted) toast.error('Stream interrupted');
    } finally {
      setIsStreaming(false);
    }
  }, [input, activeSessionId, activeLabId, isStreaming, token, loadUsage]);

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

  return (
    <div className="flex h-screen bg-ink-deep overflow-hidden">
      {/* ── Left: Hierarchical Sidebar ── */}
      <div className="w-60 shrink-0 flex flex-col border-r border-border-subtle bg-ink-base">
        <HierarchicalSidebar
          role="student"
          classes={classes}
          selectedLabId={activeLabId}
          onSelectLab={handleSelectLab}
          onJoinClass={() => setShowJoinModal(true)}
          loading={classesLoading}
        />

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

      {/* ── Middle: Session list (when lab selected) ── */}
      {activeLabId && (
        <div className="w-48 shrink-0 flex flex-col border-r border-border-subtle bg-ink-base">
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
                  <div className="flex items-center gap-2 px-3 py-2 text-xs bg-rose-500/10">
                    <span className="flex-1 truncate text-rose-300">Delete this chat?</span>
                    <button
                      onClick={() => handleDeleteSession(s.id)}
                      className="w-5 h-5 flex items-center justify-center text-rose-400 hover:text-rose-300 transition-colors cursor-pointer"
                      title="Confirm delete"
                    >
                      <CheckIcon className="w-3.5 h-3.5" />
                    </button>
                    <button
                      onClick={() => setConfirmingDeleteId(null)}
                      className="w-5 h-5 flex items-center justify-center text-cream-muted hover:text-cream transition-colors cursor-pointer"
                      title="Cancel"
                    >
                      <XIcon className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ) : (
                  <>
                    <button
                      onClick={() => handleSelectSession(s.id)}
                      className={`w-full text-left px-3 py-2 pr-14 text-xs transition-all cursor-pointer ${
                        activeSessionId === s.id
                          ? 'bg-cyan-muted text-cyan'
                          : 'text-cream-secondary hover:bg-ink-hover hover:text-cream'
                      }`}
                    >
                      <span className="block truncate">{s.title || 'Untitled Session'}</span>
                      <span className="block text-[9px] text-cream-muted mt-0.5">
                        {new Date(s.created_at).toLocaleDateString()}
                      </span>
                    </button>
                    {/* Hover actions: rename + soft-delete */}
                    <div className="absolute right-1.5 top-1/2 -translate-y-1/2 flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button
                        onClick={() => { setRenamingSessionId(s.id); setRenameTitle(s.title || ''); }}
                        className="w-5 h-5 flex items-center justify-center text-cream-muted hover:text-cream transition-colors cursor-pointer"
                        title="Rename session"
                      >
                        <PencilIcon className="w-3 h-3" />
                      </button>
                      <button
                        onClick={() => setConfirmingDeleteId(s.id)}
                        className="w-5 h-5 flex items-center justify-center text-cream-muted hover:text-rose-400 transition-colors cursor-pointer"
                        title="Delete session"
                      >
                        <TrashIcon className="w-3 h-3" />
                      </button>
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Right: Chat panel ── */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Chat header */}
        <div className="shrink-0 h-14 px-6 border-b border-border-subtle flex items-center justify-between bg-ink-base/80 backdrop-blur-sm">
          <div>
            {activeSession ? (
              <h2 className="text-sm font-semibold text-cream">{activeSession.title || 'Untitled Session'}</h2>
            ) : activeLabId ? (
              <h2 className="text-sm font-semibold text-cream-secondary">Select or create a session</h2>
            ) : (
              <h2 className="text-sm font-semibold text-cream-secondary">Select a lab to begin</h2>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-cream-muted font-mono">{username}</span>
            {isStreaming && (
              <div className="flex items-center gap-1.5 text-[10px] text-cyan">
                <div className="w-1.5 h-1.5 rounded-full bg-cyan animate-pulse" />
                streaming
              </div>
            )}
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-5">
          {!activeLabId && (
            <div className="flex items-center justify-center h-full">
              <div className="text-center max-w-xs">
                <div className="w-16 h-16 rounded-2xl gradient-cyan mx-auto mb-4 flex items-center justify-center shadow-glow">
                  <svg className="w-8 h-8 text-cream" fill="none" stroke="currentColor" strokeWidth="1.5" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M8.625 12a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 0 1-2.555-.337A5.972 5.972 0 0 1 5.41 20.97a5.969 5.969 0 0 1-.474-.065 4.48 4.48 0 0 0 .978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25Z" />
                  </svg>
                </div>
                <h3 className="font-display text-xl text-cream mb-2">Select a Lab</h3>
                <p className="text-sm text-cream-secondary">
                  Choose a lab from the sidebar to start chatting, or join a class first.
                </p>
              </div>
            </div>
          )}

          {activeLabId && !activeSessionId && sessions.length === 0 && (
            <div className="flex items-center justify-center h-full">
              <div className="text-center">
                <p className="text-cream-secondary text-sm mb-3">No sessions yet for this lab.</p>
                <button onClick={handleNewSession} className="px-4 py-2 rounded-lg gradient-cyan text-cream text-sm font-medium cursor-pointer hover:brightness-110 transition-all">
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

          {messages.map((msg) => (
            <MessageBubble key={msg.id} message={msg} />
          ))}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="shrink-0 px-6 py-4 border-t border-border-subtle bg-ink-base/80 backdrop-blur-sm">
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
                  ? 'bg-danger-muted text-danger hover:bg-danger-muted/80'
                  : 'gradient-cyan text-cream hover:brightness-110 shadow-glow disabled:opacity-40 disabled:cursor-not-allowed'
              }`}
            >
              {isStreaming ? <StopIcon className="w-4 h-4" /> : <SendIcon className="w-4 h-4" />}
            </button>
          </div>
        </div>
      </div>

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
                className="flex-1 py-2.5 rounded-lg gradient-cyan text-cream text-sm font-semibold disabled:opacity-50 cursor-pointer hover:brightness-110 transition-all">
                {isJoining ? 'Joining…' : 'Join Class'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Message Bubble ── */
function MessageBubble({ message }) {
  const isUser = message.sender === 'user';
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} gap-3 animate-fade-in`}>
      {!isUser && (
        <div className="w-7 h-7 rounded-lg gradient-cyan flex items-center justify-center shrink-0 mt-0.5 shadow-glow">
          <span className="text-cream text-[10px] font-bold">AI</span>
        </div>
      )}
      <div className={`max-w-[72%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
        isUser
          ? 'bg-cyan-muted text-cream rounded-tr-sm border border-cyan/20'
          : 'bg-ink-raised text-cream-secondary border border-border-subtle rounded-tl-sm'
      }`}>
        {message.content || <span className="inline-flex gap-1"><BlinkDot /><BlinkDot delay="150ms" /><BlinkDot delay="300ms" /></span>}
        {!isUser && message.citations?.length > 0 && (
          <div className="mt-2.5 pt-2.5 border-t border-border-subtle flex flex-wrap gap-1.5">
            {message.citations.map((c, i) => (
              <span
                key={`${c.document_id}-${c.page_no}-${i}`}
                className="inline-flex items-center gap-1 rounded-md bg-cyan-muted/60 border border-cyan/20 px-1.5 py-0.5 text-[10px] text-cream-secondary"
                title={c.filename || c.document_id}
              >
                <DocIcon className="w-2.5 h-2.5 text-cyan" />
                <span className="max-w-[180px] truncate">{c.filename || 'Source'}</span>
                {c.page_no != null && <span className="text-cream-muted">· p.{c.page_no}</span>}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function BlinkDot({ delay = '0ms' }) {
  return <span className="w-1.5 h-1.5 rounded-full bg-cream-muted animate-pulse inline-block" style={{ animationDelay: delay }} />;
}

/* ── Icons ── */
function SendIcon({ className }) {
  return <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m22 2-7 20-4-9-9-4 20-7z" /><path d="M22 2 11 13" /></svg>;
}
function StopIcon({ className }) {
  return <svg className={className} viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>;
}
function DocIcon({ className }) {
  return <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></svg>;
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
