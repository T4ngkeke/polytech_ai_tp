/** JoinModal.jsx — join a class with the teacher's 6-character invite code. */
import { useRef, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import { useT } from '../../lib/i18n';
import { Modal } from './ui';

export default function JoinModal({ onClose, onJoined }) {
  const t = useT();
  const [code, setCode] = useState(['', '', '', '', '', '']);
  const [busy, setBusy] = useState(false);
  const boxes = useRef([]);
  const set = (i, value) => {
    const chars = value.replace(/[^a-zA-Z0-9]/g, '').toUpperCase();
    const next = [...code];
    if (chars.length > 1) {
      // A pasted code fills the boxes from here.
      [...chars].slice(0, 6 - i).forEach((c, k) => { next[i + k] = c; });
      setCode(next);
      boxes.current[Math.min(5, i + chars.length)]?.focus();
      return;
    }
    next[i] = chars;
    setCode(next);
    if (chars && i < 5) boxes.current[i + 1]?.focus();
  };
  const submit = async (e) => {
    e.preventDefault();
    const value = code.join('');
    if (value.length !== 6) return;
    setBusy(true);
    try {
      const result = await api.post('/api/student/classes/join', { invite_code: value });
      toast.success(t('join.done', { cls: result.class_name }));
      onJoined();
    } catch (err) {
      toast.error(err.message || t('join.invalid'));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={t('join.title')} onClose={onClose} width="max-w-[420px]">
      <form onSubmit={submit} className="px-5 pb-5">
        <p className="mb-4 text-[14px] text-cream-secondary">{t('join.text')}</p>
        <div className="mb-5 flex justify-center gap-2">
          {code.map((c, i) => (
            <input key={i} id={`join-${i}`} ref={(el) => { boxes.current[i] = el; }} value={c} inputMode="text" autoComplete="off"
              aria-label={t('join.box', { n: i + 1 })} maxLength={6} autoFocus={i === 0}
              onChange={(e) => set(i, e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Backspace' && !c && i > 0) boxes.current[i - 1]?.focus(); }}
              className="h-12 w-10 rounded-xl border border-border-default bg-ink-raised text-center font-mono text-[18px] uppercase text-cream outline-none focus:border-cream-muted" />
          ))}
        </div>
        <button type="submit" disabled={busy || code.join('').length !== 6}
          className="w-full rounded-full bg-ink py-2.5 text-[14px] text-on-ink disabled:opacity-40">{t('join.submit')}</button>
      </form>
    </Modal>
  );
}
