/**
 * teacher/shared.jsx — Small pieces of the Teacher page shell: empty state, breadcrumb, name modal.
 */
import { useT } from '../../lib/i18n';

export function EmptyState() {
  const t = useT();
  return (
    <div className="flex items-center justify-center h-full p-4">
      <div className="text-center max-w-xs">
        <h3 className="mb-2 text-[15px] font-semibold text-cream">{t('t.empty.title')}</h3>
        <p className="text-[14px] text-cream-muted">{t('t.empty.text')}</p>
      </div>
    </div>
  );
}

// "Class › Lab" above the tabs. The class name opens the class; the lab is the
// page already shown.
export function Breadcrumb({ cls, lab, onClass, className }) {
  const t = useT();
  if (!cls) return null;
  return (
    <nav aria-label={t('t.breadcrumb')} className={className}>
      {lab ? (
        <button type="button" onClick={onClass} title={t('t.open', { name: cls.name })}
          className="-my-1 py-1 cursor-pointer hover:text-cream hover:underline underline-offset-2">
          {cls.name}
        </button>
      ) : <span aria-current="page">{cls.name}</span>}
      {lab && (
        <>
          <span aria-hidden="true" className="mx-1.5">›</span>
          <span aria-current="page" className="text-cream-secondary">{lab.name}</span>
        </>
      )}
    </nav>
  );
}

export function SimpleModal({ title, value, setValue, placeholder, onConfirm, onClose, confirmLabel }) {
  const t = useT();
  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4 animate-fade-in"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-full max-w-sm rounded-2xl border border-border-subtle bg-ink-raised p-5 shadow-elevated">
        <h3 className="mb-3 text-[15px] font-semibold text-cream">{title}</h3>
        <input autoFocus type="text" value={value} onChange={(e) => setValue(e.target.value)}
          placeholder={placeholder} aria-label={title}
          onKeyDown={(e) => { if (e.key === 'Enter') onConfirm(); if (e.key === 'Escape') onClose(); }}
          className="mb-4 w-full rounded-lg border border-border-default bg-ink-deep px-3 py-2 text-[14px] text-cream placeholder:text-cream-muted focus:border-cream-muted focus:outline-none" />
        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="rounded-lg px-3.5 py-1.5 text-[13px] text-cream-secondary hover:bg-ink-hover cursor-pointer">{t('cancel')}</button>
          <button onClick={onConfirm} disabled={!value.trim()}
            className="rounded-full bg-ink px-3.5 py-1.5 text-[13px] text-on-ink disabled:opacity-40 cursor-pointer">{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
