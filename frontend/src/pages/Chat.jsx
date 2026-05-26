/**
 * Chat.jsx — Student AI Chat page for Edu-LLM v3.
 *
 * Layout: SessionSidebar (left) + ChatWindow (right)
 * Features:
 *   - Session CRUD via /api/student/sessions
 *   - SSE streaming via @microsoft/fetch-event-source
 *   - Quota progress bar
 *   - 429 error toast for exceeded quota
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import toast from 'react-hot-toast';
import api from '../lib/api';
import useAuthStore from '../store/authStore';

export default function Chat() {
  const token = useAuthStore((s) => s.token);
  const userId = useAuthStore((s) => s.userId);

  // ── Sessions state ──
  const [sessions, setSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [sessionsLoading, setSessionsLoading] = useState(true);

  // ── Messages state ──
  const [messages, setMessages] = useState([]);
  const [messagesLoading, setMessagesLoading] = useState(false);

  // ── Input state ──
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);

  // ── Quota state ──
  const [quota, setQuota] = useState({ used: 0, limit: 0 });

  // ── Refs ──
  const messagesEndRef = useRef(null);
  const abortRef = useRef(null);
  const textareaRef = useRef(null);

  // ── Auto-scroll ──
  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  // ── Load sessions on mount ──
  useEffect(() => {
    loadSessions();
  }, []);

  // ── Load quota info ──
  useEffect(() => {
    loadQuota();
  }, []);

  async function loadSessions() {
    setSessionsLoading(true);
    try {
      const data = await api.get('/api/student/sessions');
      setSessions(data);
      if (data.length > 0 && !activeSessionId) {
        setActiveSessionId(data[0].id);
      }
    } catch {
      toast.error('Failed to load sessions');
    } finally {
      setSessionsLoading(false);
    }
  }

  async function loadQuota() {
    try {
      const data = await api.get('/api/student/usage');
      setQuota({ used: data.used, limit: data.limit });
    } catch {
      // non-critical
    }
  }

  // ── Load messages when active session changes ──
  useEffect(() => {
    if (!activeSessionId) {
      setMessages([]);
      return;
    }
    loadMessages(activeSessionId);
  }, [activeSessionId]);

  async function loadMessages(sessionId) {
    setMessagesLoading(true);
    try {
      const data = await api.get(`/api/student/sessions/${sessionId}`);
      setMessages(data.messages || []);
      // Update quota from message token sums (approximation)
    } catch {
      toast.error('Failed to load messages');
    } finally {
      setMessagesLoading(false);
    }
  }

  // ── Create new session ──
  async function handleNewSession() {
    try {
      const newSession = await api.post('/api/student/sessions', {
        title: `Chat ${new Date().toLocaleString()}`,
      });
      setSessions((prev) => [newSession, ...prev]);
      setActiveSessionId(newSession.id);
      setMessages([]);
    } catch {
      toast.error('Failed to create session');
    }
  }

  // ── Send message via SSE ──
  async function handleSend() {
    const text = input.trim();
    if (!text || !activeSessionId || isStreaming) return;

    setInput('');
    setIsStreaming(true);

    // Optimistic user message
    const userMsg = {
      id: `temp-${Date.now()}`,
      sender: 'user',
      content: text,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);

    // Placeholder for LLM response
    const llmMsgId = `llm-${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      { id: llmMsgId, sender: 'llm', content: '', created_at: new Date().toISOString() },
    ]);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      await fetchEventSource('/api/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          session_id: activeSessionId,
          message: text,
        }),
        signal: ctrl.signal,

        onopen: async (response) => {
          if (response.status === 429) {
            toast.error('Daily token quota exceeded (Too Many Requests)', {
              duration: 5000,
              icon: '⚠️',
            });
            throw new Error('Quota exceeded');
          }
          if (!response.ok) {
            const body = await response.json().catch(() => ({}));
            throw new Error(body.detail || `Error ${response.status}`);
          }
        },

        onmessage: (event) => {
          const chunk = event.data;
          if (chunk) {
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === llmMsgId
                  ? { ...msg, content: msg.content + chunk }
                  : msg
              )
            );
          }
        },

        onerror: (err) => {
          // If it's a quota error we already toasted
          if (err?.message === 'Quota exceeded') {
            throw err; // stop retrying
          }
          toast.error('Connection error. Please try again.');
          throw err; // stop retrying
        },

        onclose: () => {
          // Stream finished
          // We intentionally do NOT call loadMessages here to avoid a race condition 
          // with the backend's background task that saves the messages.
          // The optimistic UI already has the complete message history.
          setTimeout(() => loadQuota(), 500);
        },
      });
    } catch (err) {
      if (err?.name !== 'AbortError' && err?.message !== 'Quota exceeded') {
        // Remove the empty LLM placeholder on error
        setMessages((prev) => {
          const llmMsg = prev.find((m) => m.id === llmMsgId);
          if (llmMsg && !llmMsg.content) {
            return prev.filter((m) => m.id !== llmMsgId);
          }
          return prev;
        });
      }
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
    }
  }

  // ── Handle Enter key ──
  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // ── Compute quota percentage ──
  const quotaPct = quota.limit > 0 ? Math.min((quota.used / quota.limit) * 100, 100) : 0;

  return (
    <div className="flex h-[calc(100vh-var(--header-height))]">
      {/* ════════════════════════════════════════════
          SESSION SIDEBAR (LEFT)
          ════════════════════════════════════════════ */}
      <div className="w-72 shrink-0 flex flex-col border-r border-border-subtle bg-bg-secondary">
        {/* New session button */}
        <div className="p-3 border-b border-border-subtle">
          <button
            id="new-session-button"
            onClick={handleNewSession}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg border border-border-default text-sm font-medium text-text-primary hover:bg-bg-surface-hover hover:border-accent/30 transition-all duration-200 cursor-pointer group"
          >
            <PlusIcon className="w-4 h-4 text-accent group-hover:scale-110 transition-transform" />
            New Session
          </button>
        </div>

        {/* Session list */}
        <div className="flex-1 overflow-y-auto px-2 py-2 space-y-0.5">
          {sessionsLoading ? (
            <div className="flex items-center justify-center py-8">
              <div className="w-5 h-5 rounded-full border-2 border-accent border-t-transparent animate-[spin_0.8s_linear_infinite]" />
            </div>
          ) : sessions.length === 0 ? (
            <div className="text-center py-12 px-4">
              <ChatEmptyIcon className="w-10 h-10 text-text-muted mx-auto mb-3 opacity-50" />
              <p className="text-sm text-text-muted">No sessions yet</p>
              <p className="text-xs text-text-muted mt-1">Create a new session to start chatting</p>
            </div>
          ) : (
            sessions.map((session) => (
              <button
                key={session.id}
                onClick={() => setActiveSessionId(session.id)}
                className={`w-full text-left px-3 py-2.5 rounded-lg text-sm transition-all duration-150 cursor-pointer ${
                  activeSessionId === session.id
                    ? 'bg-accent-muted text-accent font-medium'
                    : 'text-text-secondary hover:bg-bg-surface-hover hover:text-text-primary'
                }`}
              >
                <div className="truncate font-medium">
                  {session.title || 'Untitled Session'}
                </div>
                <div className="text-xs text-text-muted mt-0.5">
                  {new Date(session.created_at).toLocaleDateString()}
                </div>
              </button>
            ))
          )}
        </div>
      </div>

      {/* ════════════════════════════════════════════
          CHAT WINDOW (RIGHT)
          ════════════════════════════════════════════ */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Quota bar */}
        <div className="px-6 py-3 border-b border-border-subtle bg-bg-surface/50">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-xs font-medium text-text-secondary">
              Daily Token Usage
            </span>
            <span className="text-xs text-text-muted">
              {quota.used.toLocaleString()} / {quota.limit.toLocaleString()}
            </span>
          </div>
          <div className="w-full h-2 rounded-full bg-bg-primary overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-700 ease-out"
              style={{
                width: `${quotaPct}%`,
                background:
                  quotaPct > 90
                    ? 'var(--color-danger)'
                    : quotaPct > 70
                      ? 'var(--color-accent-warm)'
                      : 'var(--color-accent)',
              }}
            />
          </div>
        </div>

        {/* Messages area */}
        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-4">
          {!activeSessionId ? (
            <div className="flex flex-col items-center justify-center h-full text-center animate-fade-in">
              <div className="w-16 h-16 rounded-2xl gradient-accent flex items-center justify-center mb-4 shadow-[var(--shadow-glow)]">
                <SparkleIcon className="w-8 h-8 text-white" />
              </div>
              <h2 className="text-xl font-bold font-[var(--font-display)] text-text-primary mb-2">
                Start a Conversation
              </h2>
              <p className="text-sm text-text-secondary max-w-sm">
                Select a session from the sidebar or create a new one to begin chatting with your AI assistant.
              </p>
            </div>
          ) : messagesLoading ? (
            <div className="flex items-center justify-center h-full">
              <div className="w-6 h-6 rounded-full border-2 border-accent border-t-transparent animate-[spin_0.8s_linear_infinite]" />
            </div>
          ) : messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-center animate-fade-in">
              <p className="text-sm text-text-muted">
                This session is empty. Send a message to get started!
              </p>
            </div>
          ) : (
            messages.map((msg, i) => (
              <div
                key={msg.id}
                className={`flex ${msg.sender === 'user' ? 'justify-end' : 'justify-start'} animate-fade-in`}
                style={{ animationDelay: `${Math.min(i * 30, 300)}ms` }}
              >
                <div
                  className={`max-w-[75%] px-4 py-3 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap ${
                    msg.sender === 'user'
                      ? 'bg-accent text-white rounded-br-md'
                      : 'bg-bg-surface border border-border-subtle text-text-primary rounded-bl-md'
                  }`}
                >
                  {msg.content}
                  {msg.sender === 'llm' && isStreaming && msg.content === '' && (
                    <span className="inline-flex gap-1 ml-1">
                      <span className="w-1.5 h-1.5 rounded-full bg-accent animate-[pulse-glow_1s_ease-in-out_infinite]" />
                      <span className="w-1.5 h-1.5 rounded-full bg-accent animate-[pulse-glow_1s_ease-in-out_0.2s_infinite]" />
                      <span className="w-1.5 h-1.5 rounded-full bg-accent animate-[pulse-glow_1s_ease-in-out_0.4s_infinite]" />
                    </span>
                  )}
                </div>
              </div>
            ))
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input area */}
        {activeSessionId && (
          <div className="px-6 py-4 border-t border-border-subtle bg-bg-surface/30">
            <div className="flex items-end gap-3">
              <textarea
                ref={textareaRef}
                id="chat-input"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Type your message… (Enter to send, Shift+Enter for new line)"
                disabled={isStreaming}
                rows={1}
                className="flex-1 px-4 py-3 rounded-xl bg-bg-primary border border-border-default text-sm text-text-primary placeholder:text-text-muted resize-none focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/30 transition-colors disabled:opacity-50 max-h-32 overflow-y-auto"
                style={{ minHeight: '48px' }}
              />
              <button
                id="chat-send-button"
                onClick={handleSend}
                disabled={!input.trim() || isStreaming}
                className="shrink-0 w-11 h-11 rounded-xl gradient-accent flex items-center justify-center text-white transition-all duration-200 hover:brightness-110 active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer shadow-[var(--shadow-glow)]"
              >
                {isStreaming ? (
                  <div className="w-4 h-4 rounded-full border-2 border-white/30 border-t-white animate-[spin_0.8s_linear_infinite]" />
                ) : (
                  <SendIcon className="w-5 h-5" />
                )}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
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

function SendIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m22 2-7 20-4-9-9-4z" /><path d="m22 2-11 11" />
    </svg>
  );
}

function SparkleIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 2L14.09 8.26L20 9.27L15.55 13.97L16.91 20L12 16.9L7.09 20L8.45 13.97L4 9.27L9.91 8.26L12 2Z" />
    </svg>
  );
}

function ChatEmptyIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="m3 21 1.9-5.7a8.5 8.5 0 1 1 3.8 3.8z" />
      <path d="M8 12h.01M12 12h.01M16 12h.01" />
    </svg>
  );
}
