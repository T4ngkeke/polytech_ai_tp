/**
 * admin/llm/SettingsSections.jsx — the setting sections of the Model settings
 * page: Answers (context shares, research limits), PDF upload (picture
 * captions) and Billing (token weights).
 */
import { useEffect, useState } from 'react';
import { NUMBERS, contextShare, numberSpec } from '../../../lib/llmSettings';
import { useT } from '../../../lib/i18n';
import { Card, Note, NumberSetting, Switch } from './parts';

const fmt = (n) => Math.round(n).toLocaleString('en-US');

function numbers(group, st, set, config, extra = {}) {
  return NUMBERS.filter((n) => n.group === group).map((n) => {
    const spec = numberSpec(n.key, config);
    return (
      <NumberSetting key={n.key} spec={spec} value={st.num[n.key]}
        onChange={(v) => set((p) => { const num = { ...p.num }; if (v === undefined) delete num[n.key]; else num[n.key] = v; return { ...p, num }; })}>
        {extra[n.key]}
      </NumberSetting>
    );
  });
}

export function AnswersSection({ st, set, config, contextTokens }) {
  const t = useT();
  const value = (k) => st.num[k] ?? numberSpec(k, config).def;
  const share = (k) => {
    const { tokens, chars } = contextShare(contextTokens, value(k));
    return <span className="mt-0.5 block text-cream-secondary">{t('a.llm.share', { tokens: fmt(tokens), chars: fmt(chars) })}</span>;
  };
  const tp = value('tp_context_percent');
  const hist = value('history_context_percent');
  const room = value('agent_total_seconds') - value('agent_research_seconds');
  const call = value('agent_call_seconds');
  return (
    <Card id="llm-answers" title={t('a.llm.answers')} text={t('a.llm.answersText')}>
      <p className="text-[14px] font-semibold text-cream">
        {t('a.llm.contextShare')} <span className="font-normal text-[13px] text-cream-muted">({t('a.llm.contextOn', { n: fmt(contextTokens) })})</span>
      </p>
      <div>{numbers('context', st, set, config, { tp_context_percent: share('tp_context_percent'), history_context_percent: share('history_context_percent') })}</div>
      {tp + hist > 75 && (
        <Note tone="warn">{t('a.llm.tpWarn', { n: tp + hist })}</Note>
      )}
      <p className="mt-1 text-[14px] font-semibold text-cream">{t('a.llm.researchTitle')}</p>
      <div>{numbers('research', st, set, config)}</div>
      {room < call && (
        <Note tone="warn">{t('a.llm.timeWarn', { room, call })}</Note>
      )}
    </Card>
  );
}

export function PdfSection({ st, set, config }) {
  const t = useT();
  const on = st.on.captions && st.on.vision;
  return (
    <Card id="llm-pdf" title={t('a.llm.pdf')} text={t('a.llm.pdfText')}>
      <div className="flex items-start gap-3">
        <Switch on={on} disabled={!st.on.vision} label={t('a.llm.captions')}
          onChange={(v) => set((p) => ({ ...p, on: { ...p.on, captions: v } }))} />
        <div className="min-w-0">
          <p className="text-[14px] font-medium text-cream">{t('a.llm.captions')}</p>
          <p className="text-[13px] text-cream-muted">{t('a.llm.captionsText')}</p>
        </div>
      </div>
      {!st.on.vision && <Note>{t('a.llm.captionsOff')}</Note>}
      {on && <div>{numbers('pdf', st, set, config)}</div>}
    </Card>
  );
}

function Weight({ id, label, help, value, onChange }) {
  const t = useT();
  const [text, setText] = useState(String(value));
  useEffect(() => { setText(String(value)); }, [value]);
  const ok = (t) => t.trim() !== '' && !Number.isNaN(Number(t)) && Number(t) >= 0 && Number(t) <= 10;
  return (
    <div className="grid grid-cols-1 gap-x-4 gap-y-1 border-t border-border-subtle py-2.5 first:border-t-0 md:grid-cols-[minmax(0,1fr)_auto]">
      <p className="text-[14px] font-medium text-cream">{label}</p>
      <div className="md:row-span-2 md:text-right">
        <input id={id} aria-label={label} value={text} inputMode="decimal" aria-invalid={!ok(text)}
          onChange={(e) => { setText(e.target.value); if (ok(e.target.value)) onChange(Number(e.target.value)); }}
          className={`w-28 rounded-xl border bg-ink-raised px-3 py-1.5 text-right text-[14px] tabular-nums text-cream focus:outline-none ${ok(text) ? 'border-border-default focus:border-cream-muted' : 'border-danger'}`} />
      </div>
      <p className="text-[13px] text-cream-muted">{help}</p>
      {!ok(text) && <p className="text-[13px] text-danger md:col-span-2">{t('a.llm.zeroToTen')}</p>}
    </div>
  );
}

export function BillingSection({ st, set }) {
  const t = useT();
  const counted = 10000 * st.alpha + 500 * st.beta;
  return (
    <Card id="llm-billing" title={t('a.llm.billing')} text={t('a.llm.billingText')}>
      <div>
        <Weight id="llm-alpha" label={t('a.llm.alpha')} value={st.alpha}
          help={t('a.llm.alphaHelp')}
          onChange={(v) => set((p) => ({ ...p, alpha: v }))} />
        <Weight id="llm-beta" label={t('a.llm.beta')} value={st.beta}
          help={t('a.llm.betaHelp')} onChange={(v) => set((p) => ({ ...p, beta: v }))} />
      </div>
      <p className="rounded-xl bg-ink-hover px-3 py-2 text-[13px] text-cream-secondary">
        {t('a.llm.example', { a: st.alpha, b: st.beta })} <b className="text-cream">{fmt(counted)}</b> {t('a.llm.counted')}
      </p>
    </Card>
  );
}
