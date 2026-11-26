/**
 * Notifications.jsx — the bell (GET /api/notifications): the latest
 * notifications with an unread count, "mark all as read". The server stores a
 * kind and a payload (services/notification_service.py); the text is written
 * here, in the interface language.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import api from '../../lib/api';
import Icon from '../../components/Icon';
import { useLanguage, useT } from '../../lib/i18n';
import { menuClass, useDismiss } from './ui';

const REFRESH_MS = 60_000;

/** One notification as [icon, text]. */
export function describe(t, n) {
  const p = n.payload || {};
  switch (n.kind) {
    case 'exam_mode': return ['lock', t(p.on ? 'note.examOn' : 'note.examOff', { cls: p.class_name || '' })];
    case 'lab_locked': return ['lock', t(p.locked ? 'note.labLocked' : 'note.labOpened', { lab: p.lab_name || '' })];
    case 'new_document': return ['file', t(p.updated ? 'note.docUpdated' : 'note.docNew',
      { file: p.filename || '', where: p.lab_name || p.class_name || '' })];
    case 'document_ready': return ['check', t(p.partial ? 'note.docPartial' : p.published ? 'note.docPublished' : 'note.docReady',
      { file: p.filename || '' })];
    case 'document_failed': return ['warning', t('note.docFailed', { file: p.filename || '' })];
    case 'quota_request': return ['users', t('note.quotaRequest', { name: p.student_name || '' })];
    case 'quota_reset': return ['refresh', t('note.quotaReset')];
    case 'quota_ignored': return ['close', t('note.quotaIgnored')];
    default: return ['bell', n.kind];
  }
}

export function relativeTime(lang, iso, now = Date.now()) {
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
  const units = [['day', 86400], ['hour', 3600], ['minute', 60]];
  const format = new Intl.RelativeTimeFormat(lang === 'zh' ? 'zh-CN' : lang, { numeric: 'auto' });
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) return format.format(Math.round(seconds / size), unit);
  }
  return format.format(0, 'minute');
}

export default function Notifications({ align = 'left' }) {
  const t = useT();
  const lang = useLanguage();
  const [open, setOpen] = useState(false);
  const [data, setData] = useState({ items: [], unread: 0 });
  const ref = useRef(null);
  useDismiss(ref, open, () => setOpen(false));
  const load = useCallback(() => api.get('/api/notifications').then(setData).catch(() => {}), []);
  useEffect(() => {
    load();
    const id = setInterval(() => { if (document.visibilityState === 'visible') load(); }, REFRESH_MS);
    return () => clearInterval(id);
  }, [load]);
  const markAll = async () => {
    await api.post('/api/notifications/read', {}).catch(() => {});
    load();
  };
  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => { setOpen(!open); if (!open) load(); }} aria-label={t('notes.title')}
        title={t('notes.title')} className="relative rounded-lg p-1.5 text-cream-muted hover:bg-ink-hover hover:text-cream">
        <Icon name="bell" className="h-[18px] w-[18px]" />
        {data.unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 min-w-[16px] rounded-full bg-danger px-1 text-center text-[10px] font-semibold leading-4 text-white">
            {data.unread > 9 ? '9+' : data.unread}
          </span>
        )}
      </button>
      {open && (
        <div className={`absolute ${align === 'right' ? 'right-0' : 'left-0'} top-full z-40 mt-1 w-[340px] max-w-[calc(100vw-24px)] ${menuClass} p-0`}>
          <div className="flex items-center justify-between px-3.5 py-2.5">
            <span className="text-[14px] font-semibold text-cream">{t('notes.title')}</span>
            {data.unread > 0 && (
              <button type="button" onClick={markAll} className="text-[12.5px] text-cream-secondary hover:text-cream">
                {t('notes.markAll')}
              </button>
            )}
          </div>
          <div className="max-h-[60vh] overflow-y-auto border-t border-border-subtle">
            {!data.items.length && <p className="px-3.5 py-6 text-center text-[13px] text-cream-muted">{t('notes.none')}</p>}
            {data.items.map((n) => {
              const [icon, text] = describe(t, n);
              return (
                <div key={n.id} className="flex gap-2.5 border-b border-border-subtle px-3.5 py-2.5 last:border-0">
                  <Icon name={icon} className="mt-0.5 h-4 w-4 shrink-0 text-cream-secondary" />
                  <div className="min-w-0 flex-1">
                    <p className="text-[13.5px] leading-snug text-cream">{text}</p>
                    <p className="mt-0.5 text-[12px] text-cream-muted">{relativeTime(lang, n.created_at)}</p>
                  </div>
                  {!n.read && <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-cyan" aria-label={t('notes.unread')} />}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
