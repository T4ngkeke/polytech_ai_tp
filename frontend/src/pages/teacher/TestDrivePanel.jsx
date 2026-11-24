/**
 * teacher/TestDrivePanel.jsx — Lab "Test drive" tab: the teacher asks like a
 * student, with the student chat's own pieces (useChat, Thread, Composer,
 * SourcePanel), so the preview looks and behaves the same: the process line,
 * citations and the cited page, Python, diagrams, follow-ups, versions, the
 * "+" menu with this lab's switches. The session is a test session
 * (POST /api/teacher/labs/{id}/test-session): left out of analytics, charged
 * to the teacher.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import { useChatScroll } from '../../lib/chatScroll';
import { useT } from '../../lib/i18n';
import useChat from '../student/useChat';
import Thread from '../student/Thread';
import Composer from '../student/Composer';
import SourcePanel from '../student/SourcePanel';
import { preloadContent } from '../student/Turns';

const ALL_ON = { upload: true, web: true, code: true, deep: true };

export default function TestDrivePanel({ lab, labName, examMode = false }) {
  const t = useT();
  const labId = lab?.id;
  const features = lab?.features && Object.keys(lab.features).length ? lab.features : ALL_ON;
  const [sessionId, setSessionId] = useState(null);
  const [starting, setStarting] = useState(false);
  const [input, setInput] = useState('');
  const [tools, setTools] = useState([]);
  const [preview, setPreview] = useState(null);
  const scrollRef = useRef(null);
  const inputRef = useRef(null);
  const composerRef = useRef(null);
  const { onScroll, update: updateScroll, follow } = useChatScroll(scrollRef);
  const chat = useChat({
    tools, follow,
    getSessionId: async () => sessionId,
    onRefusal: (refusal, text, isVersion) => { if (!isVersion) setInput((current) => current || text); },
  });
  const { messages, reset } = chat;

  useEffect(() => { preloadContent(); }, []);
  useEffect(() => { updateScroll(); }, [messages, updateScroll]);
  // Another lab: a new test.
  useEffect(() => {
    setSessionId(null);
    setInput('');
    setTools([]);
    setPreview(null);
    reset();
  }, [labId, reset]);
  useEffect(() => {
    setTools((list) => {
      const kept = list.filter((k) => features[k] && !examMode);
      return kept.length === list.length ? list : kept;
    });
  }, [features, examMode]);

  const start = useCallback(async () => {
    setStarting(true);
    try {
      const { session_id: id } = await api.post(`/api/teacher/labs/${labId}/test-session`);
      reset();
      setPreview(null);
      setSessionId(id);
    } catch {
      toast.error(t('t.test.startFailed'));
    } finally {
      setStarting(false);
    }
  }, [labId, reset, t]);

  if (!sessionId) {
    return (
      <div className="max-w-2xl rounded-2xl border border-border-subtle bg-ink-raised p-5">
        <h3 className="text-[15px] font-semibold text-cream">{t('tab.testDrive')}</h3>
        <p className="mt-1 text-[13px] text-cream-muted">{t('t.test.intro', { name: labName })}</p>
        <button type="button" onClick={start} disabled={starting}
          className="mt-4 rounded-full bg-ink px-3.5 py-1.5 text-[13px] text-on-ink disabled:opacity-50">
          {starting ? t('t.test.starting') : t('t.test.start')}
        </button>
      </div>
    );
  }

  return (
    <div className="flex h-[calc(100dvh-190px)] min-h-[28rem] overflow-hidden rounded-2xl border border-border-subtle bg-ink-deep">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex shrink-0 items-center justify-between border-b border-border-subtle px-4 py-2.5">
          <span className="min-w-0 truncate text-[14px] text-cream-secondary">{t('tab.testDrive')} · {labName}</span>
          <button type="button" onClick={start}
            className="rounded-lg px-2.5 py-1 text-[13px] text-cream-secondary hover:bg-ink-hover hover:text-cream">{t('t.test.restart')}</button>
        </div>
        {examMode && (
          <div role="status" className="shrink-0 bg-warn-soft px-4 py-2 text-center text-[13px] text-cream">
            {t('exam.bannerTest')}
          </div>
        )}
        <div ref={scrollRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-[760px] space-y-8 px-4 pb-8 pt-4">
            {!messages.length && <p className="pt-6 text-center text-[14px] text-cream-muted">{t('t.test.hint')}</p>}
            <Thread chat={chat} sessionId={sessionId} features={features} examMode={examMode} pythonOn={tools.includes('code')}
              onOpenSource={setPreview} inputRef={inputRef} rate={false} />
          </div>
        </div>
        <div className="mx-auto w-full max-w-[760px] shrink-0 px-4">
          <Composer ref={composerRef} value={input} onChange={setInput}
            onSend={(text, attachments) => { chat.send(text, attachments); setInput(''); }}
            onStop={chat.stop} streaming={chat.streaming} labId={labId} features={features} examMode={examMode}
            sessions={[]} activeSessionId={sessionId} tools={tools} setTools={setTools}
            inputRef={inputRef} />
        </div>
      </div>
      {preview && preview.sessionId === sessionId && <SourcePanel source={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}
