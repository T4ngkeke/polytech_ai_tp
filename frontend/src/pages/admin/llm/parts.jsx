/**
 * admin/llm/parts.jsx — Small pieces of the Model settings page: a section
 * card, an on/off switch, a note, and a whole-number setting with its default.
 */
import { useEffect, useState } from 'react';
import Icon from '../../../components/Icon';
import { useT } from '../../../lib/i18n';
import { Switch as UiSwitch } from '../../student/ui';

export function Card({ id, title, text, action, children }) {
  return (
    <section aria-labelledby={id} className="rounded-2xl border border-border-subtle bg-ink-raised">
      <div className="flex flex-wrap items-start justify-between gap-3 px-4 pt-4 md:px-5">
        <div className="min-w-0">
          <h3 id={id} className="text-[15px] font-semibold text-cream">{title}</h3>
          {text && <p className="mt-0.5 max-w-[65ch] text-[13px] leading-relaxed text-cream-muted">{text}</p>}
        </div>
        {action}
      </div>
      <div className="flex flex-col gap-3 px-4 pb-4 pt-3 md:px-5">{children}</div>
    </section>
  );
}

export function Switch({ on, onChange, label, disabled }) {
  return <UiSwitch checked={Boolean(on)} onChange={onChange} label={label} disabled={disabled} />;
}

export function Note({ tone = 'muted', children }) {
  const cls = { warn: 'bg-warn-soft text-gold', bad: 'bg-danger-muted text-danger', muted: 'bg-ink-hover text-cream-secondary' }[tone];
  return <p className={`rounded-xl px-3 py-2 text-[13px] leading-relaxed ${cls}`}>{children}</p>;
}

const fmt = (n) => Number(n).toLocaleString('en-US');

/** A whole number with its range; empty or the default value stores "default". */
export function NumberSetting({ spec, value, onChange, children }) {
  const t = useT();
  const shown = value ?? spec.def;
  const [text, setText] = useState(shown === null || shown === undefined ? '' : String(shown));
  useEffect(() => { setText(shown === null || shown === undefined ? '' : String(shown)); }, [shown]);
  const n = Number(text);
  const empty = text.trim() === '';
  const bad = empty ? spec.def !== null : !Number.isInteger(n) || n < spec.min || n > spec.max;
  const commit = (v) => {
    setText(v);
    const x = Number(v);
    if (v.trim() === '' && spec.def === null) onChange(undefined);
    else if (v.trim() !== '' && Number.isInteger(x) && x >= spec.min && x <= spec.max) onChange(x === spec.def ? undefined : x);
  };
  const defText = spec.def === null ? t('a.llm.none') : fmt(spec.def);
  const name = t(`a.num.${spec.key}.name`);
  return (
    <div className="grid grid-cols-1 gap-x-4 gap-y-1 border-t border-border-subtle py-2.5 first:border-t-0 md:grid-cols-[minmax(0,1fr)_auto]">
      <p className="text-[14px] font-medium text-cream">{name}</p>
      <div className="flex items-center gap-2 md:row-span-2 md:justify-end">
        {value !== undefined ? (
          <button type="button" onClick={() => commit(spec.def === null ? '' : String(spec.def))}
            title={t('a.llm.backToDefault', { value: defText })}
            className="inline-flex cursor-pointer items-center gap-1 rounded-lg px-2 py-1 text-[13px] text-cream-secondary hover:bg-ink-hover hover:text-cream">
            <Icon name="reset" className="h-3 w-3" />{t('a.llm.defaultValue', { value: defText })}
          </button>
        ) : <span className="rounded-full bg-ink-hover px-2 py-0.5 text-[12.5px] text-cream-muted">{t('a.llm.default')}</span>}
        <input id={`llm-num-${spec.key}`} inputMode="numeric" aria-label={name} value={text} aria-invalid={bad}
          placeholder={spec.def === null ? t('a.llm.none') : ''} onChange={(e) => commit(e.target.value)}
          className={`w-28 rounded-xl border bg-ink-raised px-3 py-1.5 text-right text-[14px] tabular-nums text-cream focus:outline-none ${bad ? 'border-danger' : 'border-border-default focus:border-cream-muted'}`} />
        <span className="w-12 text-[13px] text-cream-muted">{spec.unit}</span>
      </div>
      <div className="text-[13px] leading-relaxed text-cream-muted">
        {t(`a.num.${spec.key}.help`)} {t('a.llm.range', { min: fmt(spec.min), max: fmt(spec.max) })}{children}
      </div>
      {bad && <p className="text-[13px] text-danger md:col-span-2">{t('a.llm.wholeNumber', { min: fmt(spec.min), max: fmt(spec.max) })}</p>}
    </div>
  );
}
