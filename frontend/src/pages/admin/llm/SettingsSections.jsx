/**
 * admin/llm/SettingsSections.jsx — the setting sections of the Model settings
 * page: Answers (context shares, research limits), PDF upload (picture
 * captions) and Billing (token weights).
 */
import { useEffect, useState } from 'react';
import { NUMBERS, contextShare, numberSpec } from '../../../lib/llmSettings';
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
  const value = (k) => st.num[k] ?? numberSpec(k, config).def;
  const share = (k) => {
    const { tokens, chars } = contextShare(contextTokens, value(k));
    return <span className="mt-0.5 block font-mono text-cyan">≈ {fmt(tokens)} tokens · {fmt(chars)} characters</span>;
  };
  const tp = value('tp_context_percent');
  const hist = value('history_context_percent');
  const room = value('agent_total_seconds') - value('agent_research_seconds');
  const call = value('agent_call_seconds');
  return (
    <Card id="llm-answers" title="Answers" text="How much each answer may read, and how long it may take.">
      <p className="text-sm font-semibold text-cream">
        Share of the model context <span className="font-normal text-xs text-cream-muted">({fmt(contextTokens)} tokens on the student answer server)</span>
      </p>
      <div>{numbers('context', st, set, config, { tp_context_percent: share('tp_context_percent'), history_context_percent: share('history_context_percent') })}</div>
      {tp + hist > 75 && (
        <Note tone="warn">TP text and history together are {tp + hist}%. In Search and Direct the two together use at most 75% of the context, so the history gets less than set.</Note>
      )}
      <p className="mt-1 text-sm font-semibold text-cream">Research mode, per question</p>
      <div>{numbers('research', st, set, config)}</div>
      {room < call && (
        <Note tone="warn">Total time leaves {room} s after research, less than one model call ({call} s): long answers may be cut off.</Note>
      )}
    </Card>
  );
}

export function PdfSection({ st, set, config }) {
  const on = st.on.captions && st.on.vision;
  return (
    <Card id="llm-pdf" title="PDF upload" text="What the worker does after it has read a new PDF.">
      <div className="flex items-start gap-3">
        <Switch on={on} disabled={!st.on.vision} label="Picture captions"
          onChange={(v) => set((p) => ({ ...p, on: { ...p.on, captions: v } }))} />
        <div className="min-w-0">
          <p className="font-medium text-cream">Picture captions</p>
          <p className="text-xs text-cream-muted">Two or three French sentences on each picture, so search finds the page. Written by the image-view server.</p>
        </div>
      </div>
      {!st.on.vision && <Note>Image views are off, so new PDFs get no captions.</Note>}
      {on && <div>{numbers('pdf', st, set, config)}</div>}
    </Card>
  );
}

function Weight({ id, label, help, value, onChange }) {
  const [text, setText] = useState(String(value));
  useEffect(() => { setText(String(value)); }, [value]);
  const ok = (t) => t.trim() !== '' && !Number.isNaN(Number(t)) && Number(t) >= 0 && Number(t) <= 10;
  return (
    <div className="grid grid-cols-1 gap-x-4 gap-y-1 border-t border-border-subtle py-2.5 first:border-t-0 md:grid-cols-[minmax(0,1fr)_auto]">
      <p className="font-medium text-cream">{label}</p>
      <div className="md:row-span-2 md:text-right">
        <input id={id} aria-label={label} value={text} inputMode="decimal" aria-invalid={!ok(text)}
          onChange={(e) => { setText(e.target.value); if (ok(e.target.value)) onChange(Number(e.target.value)); }}
          className={`w-28 rounded-lg border bg-ink-deep px-3 py-1.5 text-right font-mono text-sm tabular-nums text-cream focus:outline-none ${ok(text) ? 'border-border-default focus:border-cyan/50' : 'border-danger'}`} />
      </div>
      <p className="text-xs text-cream-muted">{help}</p>
      {!ok(text) && <p className="text-xs text-danger md:col-span-2">Enter a number from 0 to 10.</p>}
    </div>
  );
}

export function BillingSection({ st, set }) {
  const counted = 10000 * st.alpha + 500 * st.beta;
  return (
    <Card id="llm-billing" title="Billing" text="How a model call counts against a user’s day and week limits.">
      <div>
        <Weight id="llm-alpha" label="α — weight of the input tokens" value={st.alpha}
          help="Input costs the server less than output, so it counts less. Default 0.2."
          onChange={(v) => set((p) => ({ ...p, alpha: v }))} />
        <Weight id="llm-beta" label="β — weight of the output tokens" value={st.beta}
          help="Default 1.0." onChange={(v) => set((p) => ({ ...p, beta: v }))} />
      </div>
      <p className="rounded-lg bg-ink-surface px-3 py-2 font-mono text-xs text-cream-secondary">
        10,000 input × {st.alpha} + 500 output × {st.beta} = <b className="text-cream">{fmt(counted)}</b> tokens counted
      </p>
    </Card>
  );
}
