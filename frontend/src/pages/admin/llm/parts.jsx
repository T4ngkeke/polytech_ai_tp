/**
 * admin/llm/parts.jsx — Small pieces of the Model settings page: a section
 * card, an on/off switch, a note, and a whole-number setting with its default.
 */
import { useEffect, useState } from 'react';
import Icon from '../../../components/Icon';

export function Card({ id, title, text, action, children }) {
  return (
    <section aria-labelledby={id} className="rounded-2xl border border-border-subtle bg-ink-raised shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3 px-4 pt-4 md:px-5">
        <div className="min-w-0">
          <h3 id={id} className="font-display text-lg text-cream">{title}</h3>
          {text && <p className="mt-0.5 max-w-[65ch] text-xs leading-relaxed text-cream-muted">{text}</p>}
        </div>
        {action}
      </div>
      <div className="flex flex-col gap-3 px-4 pb-4 pt-3 md:px-5">{children}</div>
    </section>
  );
}

export function Switch({ on, onChange, label, disabled }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled}
      onClick={() => onChange(!on)}
      className={`relative h-[22px] w-[38px] shrink-0 rounded-full transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed ${on ? 'bg-cyan' : 'bg-ink-elevated'}`}>
      <span className={`absolute left-[3px] top-[3px] h-4 w-4 rounded-full bg-ink-raised shadow transition-transform ${on ? 'translate-x-4' : ''}`} />
    </button>
  );
}

export function Note({ tone = 'muted', children }) {
  const cls = { warn: 'bg-gold-muted text-gold', bad: 'bg-danger-muted text-danger', muted: 'bg-ink-surface text-cream-secondary' }[tone];
  return <p className={`rounded-lg px-3 py-2 text-xs leading-relaxed ${cls}`}>{children}</p>;
}

const fmt = (n) => Number(n).toLocaleString('en-US');

/** A whole number with its range; empty or the default value stores "default". */
export function NumberSetting({ spec, value, onChange, children }) {
  const shown = value ?? spec.def;
  const [text, setText] = useState(shown === null || shown === undefined ? '' : String(shown));
  useEffect(() => { setText(shown === null || shown === undefined ? '' : String(shown)); }, [shown]);
  const n = Number(text);
  const empty = text.trim() === '';
  const bad = empty ? spec.def !== null : !Number.isInteger(n) || n < spec.min || n > spec.max;
  const commit = (t) => {
    setText(t);
    const x = Number(t);
    if (t.trim() === '' && spec.def === null) onChange(undefined);
    else if (t.trim() !== '' && Number.isInteger(x) && x >= spec.min && x <= spec.max) onChange(x === spec.def ? undefined : x);
  };
  return (
    <div className="grid grid-cols-1 gap-x-4 gap-y-1 border-t border-border-subtle py-2.5 first:border-t-0 md:grid-cols-[minmax(0,1fr)_auto]">
      <p className="font-medium text-cream">{spec.name}</p>
      <div className="flex items-center gap-2 md:row-span-2 md:justify-end">
        {value !== undefined ? (
          <button type="button" onClick={() => commit(spec.def === null ? '' : String(spec.def))}
            title={`Back to the default (${spec.def === null ? 'none' : fmt(spec.def)})`}
            className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-cyan hover:bg-cyan-muted cursor-pointer">
            <Icon name="reset" className="h-3 w-3" />default {spec.def === null ? 'none' : fmt(spec.def)}
          </button>
        ) : <span className="rounded-full bg-ink-surface px-2 py-0.5 text-xs text-cream-muted">default</span>}
        <input id={`llm-num-${spec.key}`} inputMode="numeric" aria-label={spec.name} value={text} aria-invalid={bad}
          placeholder={spec.def === null ? 'none' : ''} onChange={(e) => commit(e.target.value)}
          className={`w-28 rounded-lg border bg-ink-deep px-3 py-1.5 text-right font-mono text-sm tabular-nums text-cream focus:outline-none ${bad ? 'border-danger' : 'border-border-default focus:border-cyan/50'}`} />
        <span className="w-12 text-xs text-cream-muted">{spec.unit}</span>
      </div>
      <div className="text-xs leading-relaxed text-cream-muted">
        {spec.help} Range {fmt(spec.min)}–{fmt(spec.max)}.{children}
      </div>
      {bad && <p className="text-xs text-danger md:col-span-2">Enter a whole number from {fmt(spec.min)} to {fmt(spec.max)}.</p>}
    </div>
  );
}
