/**
 * ui.jsx — small pieces of the new student interface (2026-11 redesign):
 * menus that close on Esc and outside clicks, menu rows, switches, a modal,
 * the quota meter. Colors come from the theme tokens (index.css).
 */
import { useEffect, useRef } from 'react';
import Icon from '../../components/Icon';

/** Close on Esc and on a click outside `ref` while `open`. */
export function useDismiss(ref, open, onClose) {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose(); };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onDown);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onDown);
    };
  }, [ref, open, onClose]);
}

export const menuClass = 'rounded-[14px] border border-border-subtle bg-ink-raised p-1.5 shadow-elevated';

export function MenuItem({ icon, label, hint, onClick, disabled, danger, children, active, title }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} title={title}
      className={`flex h-9 w-full items-center gap-2.5 rounded-lg px-2.5 text-left text-[14px] ${
        danger ? 'text-danger' : 'text-cream'} ${active ? 'bg-ink-hover' : ''}
        hover:bg-ink-hover disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent`}>
      {icon && <Icon name={icon} className="h-4 w-4 shrink-0 text-cream-secondary" />}
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {hint && <span className="shrink-0 text-[12px] text-cream-muted">{hint}</span>}
      {children}
    </button>
  );
}

export function Switch({ checked, onChange, disabled, label, id }) {
  return (
    <button type="button" role="switch" id={id} aria-checked={checked} aria-label={label} disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors ${
        checked ? 'bg-ink' : 'bg-ink-elevated'} disabled:opacity-45`}>
      <span className={`inline-block h-4 w-4 rounded-full bg-ink-raised shadow transition-transform ${
        checked ? 'translate-x-[18px]' : 'translate-x-0.5'}`} />
    </button>
  );
}

export function Modal({ title, onClose, children, width = 'max-w-[720px]', label }) {
  const boxRef = useRef(null);
  useDismiss(boxRef, true, onClose);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-0 sm:p-6 sm:pt-[10vh]">
      <div ref={boxRef} role="dialog" aria-modal="true" aria-label={label || title}
        className={`flex max-h-dvh w-full ${width} flex-col overflow-hidden bg-ink-raised shadow-elevated sm:max-h-[80vh] sm:rounded-2xl sm:border sm:border-border-subtle`}>
        {title !== undefined && (
          <div className="flex shrink-0 items-center justify-between px-5 pb-2 pt-4">
            <h2 className="text-[16px] font-semibold text-cream">{title}</h2>
            <button type="button" onClick={onClose} aria-label="×"
              className="rounded-lg p-1.5 text-cream-muted hover:bg-ink-hover hover:text-cream">
              <Icon name="close" className="h-4 w-4" />
            </button>
          </div>
        )}
        {children}
      </div>
    </div>
  );
}

/** A thin usage bar: gray, amber from 80%, red at 100%. */
export function Meter({ label, value }) {
  const tone = value >= 100 ? 'bg-danger' : value >= 80 ? 'bg-gold' : 'bg-cream-muted';
  return (
    <div>
      <div className="mb-1 flex justify-between text-[12px] text-cream-secondary">
        <span>{label}</span><span className="tabular-nums">{value}%</span>
      </div>
      <div className="h-1 overflow-hidden rounded-full bg-ink-hover">
        <div className={`h-full rounded-full ${tone}`} style={{ width: `${Math.min(100, value)}%` }} />
      </div>
    </div>
  );
}

export function IconButton({ icon, label, onClick, className = '', active, ...rest }) {
  return (
    <button type="button" onClick={onClick} aria-label={label} title={label} {...rest}
      className={`inline-flex items-center justify-center rounded-lg p-1.5 text-cream-muted hover:bg-ink-hover hover:text-cream ${
        active ? 'text-cream' : ''} ${className}`}>
      <Icon name={icon} className="h-[18px] w-[18px]" />
    </button>
  );
}
