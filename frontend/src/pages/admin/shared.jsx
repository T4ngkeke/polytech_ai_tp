/**
 * admin/shared.jsx — Small layout pieces used by several Admin tabs.
 */
import { useState } from 'react';
import Icon from '../../components/Icon';
import { useT } from '../../lib/i18n';
import { Modal as UiModal } from '../student/ui';

export const inputClass = 'rounded-xl border border-border-default bg-ink-raised px-3 py-2 text-[14px] text-cream placeholder:text-cream-muted focus:border-cream-muted focus:outline-none';
export const primaryButton = 'rounded-full bg-ink px-3.5 py-1.5 text-[13px] text-on-ink hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40';
export const secondaryButton = 'rounded-full px-3.5 py-1.5 text-[13px] text-cream-secondary hover:bg-ink-hover hover:text-cream disabled:cursor-not-allowed disabled:opacity-40';
export const outlineButton = 'inline-flex items-center gap-1.5 rounded-full border border-border-default px-3.5 py-1.5 text-[13px] text-cream-secondary hover:bg-ink-hover hover:text-cream disabled:cursor-not-allowed disabled:opacity-40';
export const cardClass = 'rounded-2xl border border-border-subtle bg-ink-raised p-5';

/** "3 classes": noun is class, teacher, lab, student, day, session or message. */
export function useCount() {
  const t = useT();
  return (noun, n) => t(`a.${noun}.${n === 1 ? 'one' : 'many'}`, { n });
}

export function PageHeader({ title, subtitle, children }) {
  return (
    <div className="mb-5 flex items-start justify-between gap-4">
      <div className="min-w-0">
        <h2 className="text-[18px] font-semibold text-cream">{title}</h2>
        {subtitle && <p className="mt-1 text-[13px] text-cream-muted">{subtitle}</p>}
      </div>
      {children && <div className="flex shrink-0 items-center gap-2">{children}</div>}
    </div>
  );
}

export function SortableTh({ label, active, dir, onClick }) {
  return (
    <th className="px-4 py-2.5 text-left font-normal">
      <button type="button" onClick={onClick}
        className={`inline-flex cursor-pointer items-center gap-1 hover:text-cream ${active ? 'text-cream' : ''}`}>
        {label}
        <SortIcon className={`h-3 w-3 ${active ? 'text-cream' : 'text-cream-muted'}`} dir={active ? dir : null} />
      </button>
    </th>
  );
}

export function IconButton({ onClick, title, danger, disabled = false, children }) {
  return (
    <button type="button" onClick={onClick} title={title} aria-label={title} disabled={disabled}
      className={`cursor-pointer rounded-lg p-2 disabled:cursor-not-allowed disabled:opacity-30 ${
        danger ? 'text-cream-muted enabled:hover:bg-ink-hover enabled:hover:text-danger' : 'text-cream-muted enabled:hover:bg-ink-hover enabled:hover:text-cream'
      }`}>
      {children}
    </button>
  );
}

export function EmptyState({ icon, message }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 py-10 text-center text-cream-muted">
      <div>{icon}</div>
      <p className="text-[14px]">{message}</p>
    </div>
  );
}

export function Spinner() {
  return <div className="flex h-full items-center justify-center"><div className="h-6 w-6 animate-[spin_0.8s_linear_infinite] rounded-full border-2 border-cream-muted border-t-transparent" /></div>;
}

export function StatCard({ label, value }) {
  return (
    <div className={cardClass}>
      <p className="mb-1 text-[13px] text-cream-muted">{label}</p>
      <p className="text-[22px] font-semibold text-cream">{value ?? '—'}</p>
    </div>
  );
}

export function Field({ label, value, onChange, placeholder }) {
  return (
    <div className="space-y-1.5">
      <label className="text-[13px] text-cream-muted">{label}</label>
      <input type="text" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
        className={`w-full ${inputClass}`} />
    </div>
  );
}

// Masked input for secrets (API keys). Hidden by default; each field owns its
// own reveal toggle so showing one key never exposes the others.
export function SecretField({ label, value, onChange, placeholder }) {
  const t = useT();
  const [show, setShow] = useState(false);
  return (
    <div className="space-y-1.5">
      <label className="text-[13px] text-cream-muted">{label}</label>
      <div className="relative">
        <input type={show ? 'text' : 'password'} value={value} onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder} autoComplete="off" className={`w-full pr-10 ${inputClass}`} />
        <button type="button" onClick={() => setShow(!show)} title={show ? t('a.hide') : t('a.reveal')}
          className="absolute right-3 top-1/2 -translate-y-1/2 cursor-pointer text-cream-muted hover:text-cream">
          {show ? <Icon name="eyeOff" className="h-4 w-4" /> : <Icon name="eye" className="h-4 w-4" />}
        </button>
      </div>
    </div>
  );
}

export function Modal({ title, children, onClose }) {
  return (
    <UiModal title={title} onClose={onClose} width="max-w-md">
      <div className="overflow-y-auto px-5 pb-5 pt-2">{children}</div>
    </UiModal>
  );
}

export function ConfirmModal({ title, message, confirmLabel, danger, busy, onConfirm, onCancel }) {
  const t = useT();
  return (
    <Modal title={title} onClose={onCancel}>
      <div className="space-y-5">
        <p className="text-[14px] leading-relaxed text-cream-secondary">{message}</p>
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onCancel} disabled={busy} className={secondaryButton}>{t('cancel')}</button>
          <button type="button" onClick={onConfirm} disabled={busy}
            className={danger
              ? 'cursor-pointer rounded-full bg-danger px-3.5 py-1.5 text-[13px] text-white hover:opacity-90 disabled:opacity-40'
              : `cursor-pointer ${primaryButton}`}>
            {busy ? t('a.working') : (confirmLabel || t('p.confirm'))}
          </button>
        </div>
      </div>
    </Modal>
  );
}

export function SortIcon({ className, dir }) {
  // dir: 'asc' → up emphasized, 'desc' → down emphasized, null → neutral both
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m7 4 0 16" opacity={dir === 'asc' ? 1 : 0.4} />
      <path d="m3 8 4-4 4 4" opacity={dir === 'asc' ? 1 : 0.4} />
      <path d="m17 20 0-16" opacity={dir === 'desc' ? 1 : 0.4} />
      <path d="m21 16-4 4-4-4" opacity={dir === 'desc' ? 1 : 0.4} />
    </svg>
  );
}
