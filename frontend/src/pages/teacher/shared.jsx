/**
 * teacher/shared.jsx — Small pieces of the Teacher page shell: empty state, breadcrumb, name modal.
 */

export function EmptyState() {
  return (
    <div className="flex items-center justify-center h-full">
      <div className="text-center max-w-xs">
        <div className="w-16 h-16 rounded-2xl gradient-cyan mx-auto mb-4 flex items-center justify-center shadow-glow">
          <svg className="w-8 h-8 text-on-accent" fill="none" stroke="currentColor" strokeWidth="1.5" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 10h16M4 14h8M4 18h8" />
          </svg>
        </div>
        <h3 className="font-display text-xl text-cream mb-2">Select a node</h3>
        <p className="text-sm text-cream-secondary">Choose a class or lab to manage it.</p>
      </div>
    </div>
  );
}

// "Class › Lab" above the tabs. The class name opens the class; the lab is the
// page already shown.
export function Breadcrumb({ cls, lab, onClass, className }) {
  if (!cls) return null;
  return (
    <nav aria-label="Breadcrumb" className={className}>
      {lab ? (
        <button type="button" onClick={onClass} title={`Open ${cls.name}`}
          className="-my-1 py-1 [text-transform:inherit] cursor-pointer hover:text-cyan hover:underline underline-offset-2">
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
  return (
    <div className="fixed inset-0 bg-ink-deep/80 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-fade-in">
      <div className="bg-ink-base rounded-2xl border border-border-default shadow-elevated w-full max-w-sm p-6">
        <h3 className="font-display text-xl text-cream mb-4">{title}</h3>
        <input autoFocus type="text" value={value} onChange={(e) => setValue(e.target.value)}
          placeholder={placeholder}
          onKeyDown={(e) => { if (e.key === 'Enter') onConfirm(); if (e.key === 'Escape') onClose(); }}
          className="w-full px-4 py-3 rounded-lg bg-ink-deep border border-border-default text-cream text-sm placeholder:text-cream-muted focus:outline-none focus:border-cyan/40 mb-4 transition-colors" />
        <div className="flex gap-3">
          <button onClick={onClose} className="flex-1 py-2.5 rounded-lg border border-border-default text-cream-secondary text-sm hover:bg-ink-hover transition-all cursor-pointer">Cancel</button>
          <button onClick={onConfirm} disabled={!value.trim()} className="flex-1 py-2.5 rounded-lg gradient-cyan text-on-accent text-sm font-semibold disabled:opacity-40 cursor-pointer hover:brightness-110 transition-all">{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
