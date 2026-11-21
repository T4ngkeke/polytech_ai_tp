/**
 * teacher/StudentsPanel.jsx — Lab "Students" tab: the students of the class,
 * their rules, what the assistant remembers about them in this class
 * (GET /api/teacher/classes/{cid}/students/{sid}/memories), and removing a
 * student from the class (confirmed in the row).
 */
import { useEffect, useState } from 'react';
import api from '../../lib/api';
import { useT } from '../../lib/i18n';

function Memories({ classId, student }) {
  const t = useT();
  const [data, setData] = useState(null);
  useEffect(() => {
    api.get(`/api/teacher/classes/${classId}/students/${student.id}/memories`)
      .then(setData).catch(() => setData({ enabled: true, items: [] }));
  }, [classId, student.id]);
  if (data === null) return <p className="px-4 pb-3 text-[13px] text-cream-muted">…</p>;
  return (
    <div className="border-t border-border-subtle px-4 py-3">
      {!data.enabled && <p className="mb-2 text-[12.5px] text-gold">{t('memory.studentOff')}</p>}
      {!data.items.length && <p className="text-[13px] text-cream-muted">{t('memory.none')}</p>}
      {data.items.map((m) => (
        <div key={m.id} className="py-1.5">
          <p className="text-[13.5px] text-cream">{m.content}</p>
          {m.category && <p className="text-[12px] text-cream-muted">{m.category}</p>}
        </div>
      ))}
    </div>
  );
}

export default function StudentsPanel({ students, classId, onKick, onSetRule }) {
  const t = useT();
  const [open, setOpen] = useState(null);
  const [confirming, setConfirming] = useState(null);
  if (students.length === 0) return <p className="text-[14px] text-cream-muted">{t('students.none')}</p>;
  const button = 'rounded-lg px-3 py-1.5 text-[13px] hover:bg-ink-hover';
  return (
    <div className="max-w-3xl space-y-2">
      <h3 className="mb-3 text-[15px] font-semibold text-cream">{t('students.title', { n: students.length })}</h3>
      {students.map((s) => (
        <div key={s.id} className="overflow-hidden rounded-xl border border-border-subtle bg-ink-raised">
          <div className="flex flex-wrap items-center gap-2 px-4 py-3">
            <p className="min-w-0 flex-1 truncate text-[14px] font-medium text-cream">{s.username}</p>
            {confirming === s.id ? (
              <>
                <span className="text-[13px] text-cream-secondary">{t('students.kickConfirm', { name: s.username })}</span>
                <button type="button" onClick={() => { setConfirming(null); onKick(s.id); }} className="rounded-lg bg-danger px-3 py-1.5 text-[13px] text-white">{t('students.kick')}</button>
                <button type="button" onClick={() => setConfirming(null)} className={`${button} text-cream-secondary`}>{t('cancel')}</button>
              </>
            ) : (
              <>
                <button type="button" onClick={() => setOpen(open === s.id ? null : s.id)} aria-expanded={open === s.id}
                  className={`${button} text-cream-secondary`}>{t('memory.title')}</button>
                <button type="button" onClick={() => onSetRule(s)} className={`${button} text-cream-secondary`}>{t('students.rules')}</button>
                <button type="button" onClick={() => setConfirming(s.id)} className={`${button} text-danger`}>{t('students.kick')}</button>
              </>
            )}
          </div>
          {open === s.id && <Memories classId={classId} student={s} />}
        </div>
      ))}
    </div>
  );
}
