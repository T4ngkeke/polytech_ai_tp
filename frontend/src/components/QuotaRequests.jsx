/**
 * QuotaRequests.jsx — students who reached a token limit and asked for a reset.
 *
 * Data: GET /api/teacher/quota-requests (today's open requests, students of
 * this teacher's classes). Reset: POST /api/teacher/classes/{classId}/students/
 * {studentId}/quota-refill gives a full day and week again; usage is kept.
 * Loaded again every minute and when the teacher comes back to the tab.
 */
import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';

import api from '../lib/api';
import { percent } from '../lib/quota';

const REFRESH_MS = 60_000;

export default function QuotaRequests({ onReset }) {
  const [requests, setRequests] = useState([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(null);  // a user id, or 'all'

  const load = useCallback(async () => {
    try {
      setRequests(await api.get('/api/teacher/quota-requests'));
    } catch {
      // Keep the last list; the notice is not worth an error message.
    }
  }, []);

  useEffect(() => {
    load();
    const refresh = () => { if (document.visibilityState === 'visible') load(); };
    const id = setInterval(refresh, REFRESH_MS);
    document.addEventListener('visibilitychange', refresh);
    window.addEventListener('focus', refresh);
    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', refresh);
      window.removeEventListener('focus', refresh);
    };
  }, [load]);

  const reset = async (list) => {
    setBusy(list.length === 1 ? list[0].user_id : 'all');
    try {
      for (const r of list) {
        await api.post(`/api/teacher/classes/${r.classes[0].id}/students/${r.user_id}/quota-refill`);
      }
      toast.success(list.length === 1 ? `${list[0].username} can ask again` : `${list.length} students can ask again`);
      await load();
      onReset?.();
    } catch (err) {
      toast.error(err.message);
      load();
    } finally {
      setBusy(null);
    }
  };

  if (!requests.length) return null;
  const n = requests.length;
  return (
    <div className="shrink-0 border-b border-gold/40 bg-gold-muted px-4 py-2 md:px-8 text-xs text-cream">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className="flex-1 min-w-[12rem]">
          {n === 1 ? `${requests[0].username} reached a limit and asks for a reset.`
            : `${n} students reached a limit and ask for a reset.`}
        </span>
        <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
          className="cursor-pointer rounded-md px-2 py-1 text-cream-secondary hover:text-cream hover:bg-ink-hover">
          {open ? 'Hide' : 'Show'}
        </button>
        <button type="button" onClick={() => reset(requests)} disabled={busy !== null}
          title="A full day and week again for each of them; usage is kept"
          className="cursor-pointer rounded-md border border-border-default bg-ink-base px-2.5 py-1 hover:bg-ink-hover disabled:opacity-50">
          {n === 1 ? 'Reset' : `Reset all ${n}`}
        </button>
      </div>
      {open && (
        <ul className="mt-2 space-y-1">
          {requests.map((r) => (
            <li key={r.user_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md bg-ink-base/60 px-2 py-1.5">
              <span className="font-medium">{r.username}</span>
              <span className="text-cream-muted">{r.classes.map((c) => c.name).join(', ')}</span>
              <span className={`font-mono ${r.blocked === 'day' ? 'text-danger' : 'text-cream-secondary'}`}>
                Today {percent(r.used, r.limit)}%
              </span>
              <span className={`font-mono ${r.blocked === 'week' ? 'text-danger' : 'text-cream-secondary'}`}>
                Week {percent(r.week_used, r.week_limit)}%
              </span>
              <span className="text-cream-muted">
                asked {new Date(r.asked_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </span>
              <button type="button" onClick={() => reset([r])} disabled={busy !== null}
                className="ml-auto cursor-pointer rounded-md border border-border-default px-2 py-0.5 hover:bg-ink-hover disabled:opacity-50">
                Reset
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
