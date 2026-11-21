/**
 * teacher/TestDrivePanel.jsx — Lab "Test drive" tab: chat with the real agent as a preview.
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import { createMessageId, createRequestId } from '../../lib/messageId';
import { interpretStreamEvent, openStream } from '../../lib/streamEvents';
import { applyToMessage } from '../../lib/streamMessage';
import { MessageBubble } from '../../components/ChatMessage';
import { PdfPreviewModal } from '../../components/PdfPreview';
import useAuthStore from '../../store/authStore';
import { offersResearch } from '../../lib/answerModes';
import { useChatScroll } from '../../lib/chatScroll';
import { useT } from '../../lib/i18n';

// Teacher test-drive — opens an is_test session on the lab and chats through
// the real agent as a preview. The server excludes these writes from analytics,
// and the token cost is charged to the teacher, not a student.
export default function TestDrivePanel({ labId, labName, classId, examMode = false }) {
  // The same agent, stream and message bubbles as the student chat: research
  // phases, the answer draft, a question back with clickable options, and
  // cited pages that open in the source preview.
  const t = useT();
  const { token } = useAuthStore();
  const [sessionId, setSessionId] = useState(null);
  const [starting, setStarting] = useState(false);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [phase, setPhase] = useState(null);
  const [preview, setPreview] = useState(null);
  // Exam mode: the files of this lab the class keeps open (0: Direct only).
  const [examFiles, setExamFiles] = useState(0);
  useEffect(() => {
    if (!examMode || !classId) { setExamFiles(0); return; }
    api.get(`/api/teacher/classes/${classId}/exam-files`)
      .then((files) => setExamFiles(files.filter((f) => f.exam_allowed && (!f.lab_id || f.lab_id === labId)).length))
      .catch(() => setExamFiles(0));
  }, [examMode, classId, labId]);
  const directOnly = examMode && !examFiles;
  const abortRef = useRef(null);
  const scrollRef = useRef(null);
  const { onScroll, update: updateScroll, follow } = useChatScroll(scrollRef);

  // Reset (and abort any in-flight stream) when the selected lab changes.
  useEffect(() => {
    setSessionId(null);
    setMessages([]);
    setInput('');
    setIsStreaming(false);
    setPhase(null);
    setPreview(null);
    return () => abortRef.current?.abort();
  }, [labId]);

  useEffect(() => {
    updateScroll();
  }, [messages, updateScroll]);

  const start = useCallback(async () => {
    setStarting(true);
    try {
      const { session_id } = await api.post(`/api/teacher/labs/${labId}/test-session`);
      setSessionId(session_id);
      setMessages([]);
    } catch {
      toast.error(t('t.test.startFailed'));
    } finally {
      setStarting(false);
    }
  }, [labId, t]);

  // picked: an option clicked under a question back, or a question sent again
  // (again after an uncited answer); otherwise the input box.
  const send = useCallback(async (picked) => {
    const text = (typeof picked === 'string' ? picked : input).trim();
    if (!text || isStreaming || !sessionId) return;

    const assistantMsgId = createMessageId();
    setMessages((prev) => [
      ...prev,
      { id: createMessageId(), sender: 'user', content: text },
      { id: assistantMsgId, sender: 'llm', content: '' },
    ]);
    follow('pin');
    if (typeof picked !== 'string') setInput('');
    setIsStreaming(true);

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      // Same agent endpoint the students hit — the server bypasses the
      // membership check for an owning teacher's is_test session.
      await fetchEventSource('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ session_id: sessionId, message: text, client_request_id: createRequestId(),
          // One entry, as for students: the agent decides whether to search (exam mode is applied by the server).
          mode: directOnly ? 'direct' : 'research' }),
        signal: ctrl.signal,
        openWhenHidden: true,
        onopen: openStream,
        onmessage(ev) {
          const action = interpretStreamEvent(ev);
          switch (action.type) {
            case 'status':
              setPhase(action.phase);
              if (action.mode) {
                setMessages((prev) => prev.map((m) => (m.id === assistantMsgId ? { ...m, answer_mode: action.mode } : m)));
              }
              return;
            case 'done':
              setPhase(null);
              setIsStreaming(false);
              return;
            case 'error':
              setIsStreaming(false);
              setMessages((prev) => prev.map((m) => (m.id === assistantMsgId ? { ...m, draft: '' } : m)));
              toast.error(action.detail);
              return;
            default:
              setMessages((prev) => prev.map((m) => (m.id === assistantMsgId ? applyToMessage(m, action) : m)));
          }
        },
        onerror(err) { throw err; },
      });
    } catch (err) {
      if (err?.refusal) toast.error(err.refusal.message, { duration: 8000 });
      else if (!ctrl.signal.aborted) toast.error(t('t.test.interrupted'));
    } finally {
      // An unchecked draft never stays on screen once the stream ends.
      setMessages((prev) => prev.map((m) => (m.id === assistantMsgId && m.draft ? { ...m, draft: '' } : m)));
      setPhase(null);
      setIsStreaming(false);
    }
  }, [input, isStreaming, sessionId, token, directOnly, follow, t]);

  if (!sessionId) {
    return (
      <div className="max-w-2xl rounded-2xl border border-border-subtle bg-ink-raised p-5">
        <h3 className="text-[15px] font-semibold text-cream">{t('tab.testDrive')}</h3>
        <p className="mt-1 text-[13px] text-cream-muted">
          {t('t.test.intro', { name: labName })}
        </p>
        <button
          onClick={start}
          disabled={starting}
          className="mt-4 rounded-full bg-ink px-3.5 py-1.5 text-[13px] text-on-ink disabled:opacity-50 cursor-pointer"
        >
          {starting ? t('t.test.starting') : t('t.test.start')}
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full min-h-[24rem] rounded-2xl bg-ink-raised border border-border-subtle overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border-subtle">
        <span className="min-w-0 truncate text-[14px] font-semibold text-cream">{t('tab.testDrive')} · {labName}</span>
        <button onClick={start} className="rounded-lg px-2.5 py-1 text-[13px] text-cream-secondary hover:bg-ink-hover hover:text-cream cursor-pointer">{t('t.test.restart')}</button>
      </div>
      <div ref={scrollRef} onScroll={onScroll} className="relative flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <p className="text-[14px] text-cream-muted">{t('t.test.hint')}</p>
        )}
        {messages.map((m, i) => {
          const last = i === messages.length - 1;
          return (
            <div key={m.id} data-latest-answer={last && m.sender === 'llm' ? '' : undefined}>
            <MessageBubble message={m} sessionId={sessionId} onOpenSource={setPreview}
              streaming={isStreaming && m.sender === 'llm' && last}
              phase={isStreaming && m.sender === 'llm' && last ? phase : null}
              canRate={false}
              onPick={!isStreaming && last && m.clarification ? send : null}
              onResearch={!isStreaming && !directOnly && last && offersResearch(m) ? () => {
                const question = messages.slice(0, i).reverse().find((x) => x.sender === 'user');
                if (question) send(question.content);
              } : null} />
            </div>
          );
        })}
      </div>
      <div className="flex gap-2 p-3">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
          placeholder={t('t.test.placeholder')} aria-label={t('t.test.placeholder')}
          className="min-w-0 flex-1 px-3 py-2 rounded-lg bg-ink-deep border border-border-subtle text-[14px] text-cream placeholder:text-cream-muted focus:outline-none focus:border-cream-muted"
        />
        <button
          onClick={send}
          disabled={isStreaming || !input.trim()}
          className="rounded-full bg-ink px-4 py-2 text-[13px] text-on-ink disabled:opacity-50 cursor-pointer"
        >
          {t('send')}
        </button>
      </div>
      {preview && <PdfPreviewModal source={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}
