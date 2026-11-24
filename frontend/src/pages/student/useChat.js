/**
 * useChat.js — one conversation with the research agent: sending a question
 * (or a new version of the last one), the stream events, the Python runs the
 * agent asks this browser for, and the answer's fields. Shared by the
 * student chat and the teacher's test drive, so both behave the same.
 *
 * getSessionId(): the conversation to send to (it may create one).
 * Callbacks: onDone (an answer was saved), onTitle(sessionId, title),
 * onStatus(action) (exam mode may have changed), onRefusal(refusal, text,
 * isVersion) (the question was refused before it started).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import toast from 'react-hot-toast';
import useAuthStore from '../../store/authStore';
import { t as translate } from '../../lib/i18n';
import { createMessageId, createRequestId } from '../../lib/messageId';
import { interpretStreamEvent, openStream } from '../../lib/streamEvents';
import { applyToMessage } from '../../lib/streamMessage';
import { runForAgent } from '../../lib/python';

const isLocalId = (id) => typeof id === 'string' && id.startsWith('local-');

export default function useChat({ getSessionId, tools = [], onDone, onTitle, onStatus, onRefusal, follow }) {
  const token = useAuthStore((s) => s.token);
  const [messages, setMessages] = useState([]);
  const [streaming, setStreaming] = useState(false);
  const [chosenVersion, setChosenVersion] = useState({});
  const abortRef = useRef(null);
  const streamedIds = useRef(new Set());
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const callbacks = useRef({});
  callbacks.current = { onDone, onTitle, onStatus, onRefusal, follow, getSessionId };

  useEffect(() => () => abortRef.current?.abort(), []);

  const stop = useCallback(() => { abortRef.current?.abort(); setStreaming(false); }, []);

  /** Show another conversation (null: none). */
  const reset = useCallback((list = []) => {
    abortRef.current?.abort();
    setStreaming(false);
    setChosenVersion({});
    setMessages(list);
  }, []);

  const send = useCallback(async (text, attachments = [], { versionOf: asked = null, toolsOverride = null } = {}) => {
    // Only a file, no words: a short question goes with it (the server needs one).
    const message = (text || '').trim() || (attachments.length ? translate('ask.attachmentOnly') : '');
    if (!message || streaming) return;
    const cb = callbacks.current;
    const sessionId = await cb.getSessionId();
    if (!sessionId) return;
    // A question the server never saved (it failed before it started) has no
    // versions: it is taken off the screen and asked again as a new question.
    let versionOf = asked;
    if (isLocalId(versionOf)) {
      const unsaved = versionOf;
      setMessages((prev) => {
        const at = prev.findIndex((m) => m.id === unsaved);
        return at >= 0 ? prev.slice(0, at) : prev;
      });
      versionOf = null;
    }
    const userId = createMessageId();
    const answerId = createMessageId();
    streamedIds.current.add(answerId);
    const now = new Date().toISOString();
    const root = versionOf ? (messagesRef.current.find((m) => m.id === versionOf)?.version_of || versionOf) : null;
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
    // A new question follows the current versions (the ones the model reads):
    // an older version the student was looking at is put back to the current.
    if (versionOf) setChosenVersion((v) => ({ ...v, [root]: undefined }));
    else setChosenVersion({});
    cb.follow?.('pin');
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
              if (action.type === 'status') callbacks.current.onStatus?.(action);
              return;
            case 'code':
              update(action);
              // Python for this browser: run it, send the output back to the agent.
              runForAgent(action.id, action.code, (phase) => update({ type: 'code_phase', id: action.id, phase }))
                .then((output) => update({ type: 'code_output', id: action.id,
                output: { status: output.error || /Traceback|Error:/.test(output.stderr || '') ? 'error' : 'ok', ...output } }));
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
              callbacks.current.onDone?.();
              return;
            case 'question':
              // The question is saved: its real id, for a new version of it.
              setMessages((prev) => prev.map((m) => (m.id === userId ? { ...m, id: action.userMessageId } : m)));
              return;
            case 'title':
              callbacks.current.onTitle?.(action.sessionId || sessionId, action.title);
              return;
            case 'follow_ups':
              setMessages((prev) => prev.map((m) => (m.id === (action.messageId || realAnswerId) ? applyToMessage(m, action) : m)));
              return;
            case 'error': {
              if (action.code === 'busy' || action.code === 'replaced') {
                // Another window of this conversation was first: nothing was
                // asked, the question goes back into the box (catch below).
                const busy = new Error(action.code);
                busy.refusal = { message: translate(action.code === 'busy' ? 'chat.busy' : 'chat.changedElsewhere'),
                  reload: 'conversation' };
                throw busy;
              }
              setStreaming(false);
              update({ type: 'draft_reset' });
              if (action.code === 'stopped') {
                setMessages((prev) => prev.map((m) => (m.id === answerId ? { ...m, stopped_elsewhere: true } : m)));
                return;
              }
              toast.error(action.detail);
              return;
            }
            default:
          }
        },
        onerror(err) { throw err; },
      });
    } catch (err) {
      if (err?.refusal) {
        // Nothing was asked: take the question back off the screen.
        streamedIds.current.delete(answerId);
        setMessages((prev) => prev.filter((m) => m.id !== userId && m.id !== answerId)
          .map((m) => (versionOf && m.superseded_at === now ? { ...m, superseded_at: null } : m)));
        toast.error(err.refusal.messageKey ? translate(err.refusal.messageKey) : err.refusal.message, { duration: 8000 });
        callbacks.current.onRefusal?.(err.refusal, message, !!versionOf);
      } else if (!ctrl.signal.aborted) toast.error(translate('error.stream'));
    } finally {
      setMessages((prev) => prev.map((m) => (m.id === answerId || m.id === realAnswerId
        ? { ...m, draft: '', finished_at: m.finished_at || Date.now() } : m)));
      setStreaming(false);
    }
  }, [streaming, tools, token]);

  return { messages, setMessages, streaming, send, stop, reset, streamedIds, chosenVersion, setChosenVersion };
}
