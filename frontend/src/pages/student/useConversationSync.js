/**
 * useConversationSync.js — keep a conversation in step with the student's
 * other windows (a phone and a laptop, two tabs).
 *
 * While the page is visible and not receiving its own answer, it asks the
 * server every POLL_MS (routers/chat.py GET /sessions/{id}/state, also when
 * the window comes back to the front) whether an answer is being written and
 * which questions the conversation has:
 * - an answer written in another window: `elsewhere` is true (the page shows
 *   a note with a Stop button and does not send);
 * - that answer finished, or the questions differ from the ones shown (asked,
 *   edited or stopped elsewhere): the conversation is loaded again (`load`).
 * The server allows one answer at a time per conversation, so nothing is
 * lost; this only makes every window show the same thing.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import api from '../../lib/api';
import { needsReload } from '../../lib/conversationSync';

export const POLL_MS = 2000;
export default function useConversationSync({ sessionId, streaming, messages, setMessages, load }) {
  const [elsewhere, setElsewhere] = useState(false);
  const live = useRef({});
  live.current = { streaming, messages, setMessages, load };
  const checkRef = useRef(() => {});

  useEffect(() => {
    setElsewhere(false);
    if (!sessionId) return undefined;
    let ended = false;
    let timer = null;
    let wasRunning = false;
    let busy = false;
    let again = false;     // asked for during a check: one more right after it
    const schedule = () => { clearTimeout(timer); if (!ended) timer = setTimeout(check, POLL_MS); };
    async function check() {
      if (ended) return;
      if (busy) { again = true; return; }
      // Hidden: no requests (the check runs when the window comes back). Its
      // own answer: that stream is the news. wasRunning stays, so an answer
      // finished elsewhere meanwhile is loaded on return.
      if (document.visibilityState !== 'visible' || live.current.streaming) {
        if (live.current.streaming) { wasRunning = false; setElsewhere(false); }
        schedule();
        return;
      }
      busy = true;
      try {
        const state = await api.get(`/api/chat/sessions/${sessionId}/state`);
        if (ended || live.current.streaming) return;
        if (needsReload(state, live.current.messages, wasRunning)) {
          const list = await live.current.load(sessionId);
          if (ended || live.current.streaming) return;
          live.current.setMessages(list);
        }
        wasRunning = state.running;
        if (!ended) setElsewhere(state.running);
      } catch { /* the next check tries again */ } finally {
        busy = false;
        if (again) { again = false; check(); } else schedule();
      }
    }
    checkRef.current = () => { clearTimeout(timer); check(); };
    const onFront = () => { if (document.visibilityState === 'visible') checkRef.current(); };
    document.addEventListener('visibilitychange', onFront);
    window.addEventListener('focus', onFront);
    check();
    return () => {
      ended = true;
      clearTimeout(timer);
      checkRef.current = () => {};
      document.removeEventListener('visibilitychange', onFront);
      window.removeEventListener('focus', onFront);
    };
  }, [sessionId]);

  /** Ask the server now (after a refusal, after Stop). A short wait first: the
   * page may not have drawn the end of its own refused send yet. */
  const checkNow = useCallback(() => { setTimeout(() => checkRef.current(), 250); }, []);

  /** Stop the answer another window is receiving. */
  const stopElsewhere = useCallback(async () => {
    if (!sessionId) return;
    try { await api.post(`/api/chat/sessions/${sessionId}/stop`, {}); } catch { /* checked below */ }
    checkRef.current();
  }, [sessionId]);

  return { elsewhere, checkNow, stopElsewhere };
}
