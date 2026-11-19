/**
 * admin/shared.jsx — Small layout pieces used by several Admin tabs.
 */
import { useState } from 'react';
import Icon from '../../components/Icon';

export function PageHeader({ title, subtitle, children }) {
  return (
    <div className="flex items-start justify-between gap-4 mb-6">
      <div>
        <h2 className="text-xl font-display text-cream">{title}</h2>
        {subtitle && <p className="text-xs text-cream-muted mt-1">{subtitle}</p>}
      </div>
      {children && <div className="flex items-center gap-2 shrink-0">{children}</div>}
    </div>
  );
}

export function SortableTh({ label, active, dir, onClick }) {
  return (
    <th className="text-left font-medium px-4 py-3">
      <button onClick={onClick} className="inline-flex items-center gap-1 uppercase tracking-widest hover:text-cream transition-colors cursor-pointer">
        {label}
        <SortIcon className={`w-3 h-3 transition-colors ${active ? 'text-cyan' : 'text-cream-faint'}`} dir={active ? dir : null} />
      </button>
    </th>
  );
}

export function IconButton({ onClick, title, danger, disabled = false, children }) {
  return (
    <button type="button" onClick={onClick} title={title} aria-label={title} disabled={disabled}
      className={`p-2 rounded-lg transition-colors cursor-pointer disabled:cursor-not-allowed disabled:opacity-30 ${
        danger ? 'text-cream-muted enabled:hover:text-danger enabled:hover:bg-danger-muted' : 'text-cream-muted enabled:hover:text-cyan enabled:hover:bg-cyan-muted'
      }`}>
      {children}
    </button>
  );
}

export function EmptyState({ icon, message }) {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center gap-3 text-cream-muted">
      <div className="text-cream-faint">{icon}</div>
      <p className="text-sm">{message}</p>
    </div>
  );
}

export function Spinner() {
  return <div className="flex items-center justify-center h-full"><div className="w-6 h-6 rounded-full border-2 border-cyan border-t-transparent animate-[spin_0.8s_linear_infinite]" /></div>;
}

export function StatCard({ label, value }) {
  return (
    <div className="bg-ink-raised rounded-xl border border-border-subtle p-4">
      <p className="text-[10px] uppercase tracking-widest text-cream-muted mb-1">{label}</p>
      <p className="text-2xl font-display text-cream">{value ?? '—'}</p>
    </div>
  );
}

export function Field({ label, value, onChange, placeholder }) {
  return (
    <div className="space-y-1.5">
      <label className="mono-label">{label}</label>
      <input type="text" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
        className="w-full px-4 py-3 rounded-lg bg-ink-deep border border-border-default text-cream text-sm font-mono placeholder:text-cream-muted focus:outline-none focus:border-cyan/40 transition-colors" />
    </div>
  );
}

// Masked input for secrets (API keys). Hidden by default; each field owns its
// own reveal toggle so showing one key never exposes the others.
export function SecretField({ label, value, onChange, placeholder }) {
  const [show, setShow] = useState(false);
  return (
    <div className="space-y-1.5">
      <label className="mono-label">{label}</label>
      <div className="relative">
        <input type={show ? 'text' : 'password'} value={value} onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder} autoComplete="off"
          className="w-full px-4 py-3 pr-10 rounded-lg bg-ink-deep border border-border-default text-cream text-sm placeholder:text-cream-muted focus:outline-none focus:border-cyan/40 font-mono transition-colors" />
        <button type="button" onClick={() => setShow(!show)} title={show ? 'Hide' : 'Reveal'}
          className="absolute right-3 top-1/2 -translate-y-1/2 text-cream-muted hover:text-cream cursor-pointer">
          {show ? <Icon name="eyeOff" className="w-4 h-4" /> : <Icon name="eye" className="w-4 h-4" />}
        </button>
      </div>
    </div>
  );
}

export function Modal({ title, children, onClose }) {
  return (
    <div className="fixed inset-0 bg-ink-deep/80 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-fade-in">
      <div className="bg-ink-base rounded-2xl border border-border-default shadow-elevated w-full max-w-md p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-display text-xl text-cream">{title}</h3>
          <button onClick={onClose} className="text-cream-muted hover:text-cream cursor-pointer text-lg leading-none">×</button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function ConfirmModal({ title, message, confirmLabel = 'Confirm', danger, busy, onConfirm, onCancel }) {
  return (
    <Modal title={title} onClose={onCancel}>
      <div className="space-y-5">
        <p className="text-sm text-cream-secondary leading-relaxed">{message}</p>
        <div className="flex gap-3">
          <button onClick={onCancel} disabled={busy}
            className="flex-1 py-2.5 rounded-lg bg-ink-surface border border-border-default text-cream-secondary text-sm font-medium hover:text-cream hover:bg-ink-hover transition-colors cursor-pointer disabled:opacity-40">
            Cancel
          </button>
          <button onClick={onConfirm} disabled={busy}
            className={`flex-1 py-2.5 rounded-lg text-on-accent text-sm font-semibold transition-all cursor-pointer disabled:opacity-40 ${
              danger ? 'bg-danger hover:bg-danger-hover' : 'gradient-cyan hover:brightness-110'
            }`}>
            {busy ? 'Working…' : confirmLabel}
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
