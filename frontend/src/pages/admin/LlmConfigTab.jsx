/**
 * admin/LlmConfigTab.jsx — Admin "LLM Config" tab: system map, node settings and model servers.
 */
import { useState, useEffect, useCallback } from 'react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import { contextLength, serverState } from '../../lib/modelServers';
import Icon from '../../components/Icon';
import { PageHeader, Spinner, Field, SecretField } from './shared';

/* ═══════════════════ LLM CONFIG TAB — system map ══════════════════ */
// The model-routing table drawn as the system it configures: grey source
// nodes (students / worker) call the model slots; an empty slot either
// inherits the main LLM (dashed gold return edge) or is simply off. Click a
// node to edit just that slot — each panel saves independently (the backend
// PUT is a partial upsert).

// Who calls the models. Each node lists its callers in `sources`: a source
// or another node (the research agent calls embedding, reranker and vision).
const MAP_SOURCES = [
  { id: 'students', x: 10, y: 14, label: 'Students', sub: 'questions in a lab' },
  { id: 'teachers', x: 10, y: 48, label: 'Teachers', sub: 'figure descriptions' },
  { id: 'worker', x: 10, y: 82, label: 'Worker', sub: 'document uploads' },
];

// status(config): 'live' (the default model itself) · 'set' (own model/endpoint) ·
// 'inherits' (empty: uses the default model) · 'off' (feature disabled) ·
// 'deployment' (empty: uses the deployment setting).
const MAP_NODES = [
  {
    id: 'agent', x: 34, y: 14, icon: 'bot', name: 'Research agent', sources: ['students'],
    tag: 'Answers every student question in a lab: it searches and reads the course PDFs, '
      + 'may ask the student back, then answers with citations. The limits below apply to each question.',
    status: (c) => (c.agent_model ? 'set' : 'inherits'),
    fields: [
      { key: 'agent_model', label: 'Model Name', placeholder: 'inherits the default model' },
      { key: 'agent_base_url', label: 'Base URL', placeholder: 'inherits the default model' },
      { key: 'agent_api_key', label: 'API Key', placeholder: 'inherits the default model', secret: true },
      // Research limits: default and range come from the backend (research_limits).
      { key: 'agent_max_tool_calls', label: 'Course tool calls', limit: true },
      { key: 'agent_max_visual_calls', label: 'Image views', limit: true },
      { key: 'agent_research_seconds', label: 'Research time (s)', limit: true },
      { key: 'agent_total_seconds', label: 'Total time, answer included (s)', limit: true },
      { key: 'agent_call_seconds', label: 'One model call (s)', limit: true },
      { key: 'agent_max_input_tokens', label: 'Input tokens per model call', limit: true },
      { key: 'tp_context_percent', label: 'TP text, % of the model context (Search, Direct)', limit: true },
      { key: 'history_context_percent', label: 'Chat history, % of the model context', limit: true },
    ],
  },
  {
    id: 'vlm', x: 34, y: 48, icon: 'eye', name: 'Vision (VLM)', sources: ['agent', 'teachers', 'worker'],
    tag: 'Looks at images: page images and figures the research agent views, figure descriptions '
      + 'teachers ask for, and the picture captions the worker writes after reading a PDF (when on). '
      + 'Empty model = off; it never falls back to a text-only model.',
    status: (c) => (c.vlm_model ? 'set' : 'off'),
    fields: [
      { key: 'vlm_model', label: 'Model Name', placeholder: 'empty = off' },
      { key: 'vlm_base_url', label: 'Base URL', placeholder: 'inherits the default model URL' },
      { key: 'vlm_api_key', label: 'API Key', placeholder: 'inherits the default model key', secret: true },
      { key: 'picture_captions', label: 'Picture captions at upload', placeholder: 'on / off (empty = off)',
        hint: () => 'A short French caption for each picture of a new PDF, used by search only.' },
      { key: 'picture_caption_priority', label: 'Caption priority', placeholder: 'empty = none sent',
        hint: () => 'vLLM priority of caption requests; students (0) go first when it is higher. '
          + 'Needs --scheduling-policy priority on the model server; leave empty otherwise.' },
      { key: 'picture_caption_budget_seconds', label: 'Caption time per document (s)', placeholder: '900' },
    ],
  },
  {
    id: 'chat', x: 85, y: 48, icon: 'message', name: 'Default model', sources: [],
    tag: 'Every empty model slot uses this model (dashed lines). It also holds the billing weights '
      + 'α/β applied to every model call.',
    status: () => 'live',
    fields: [
      { key: 'model', label: 'Model Name', placeholder: 'served model name' },
      { key: 'base_url', label: 'Base URL', placeholder: 'http://model-server:8000/v1' },
      { key: 'api_key', label: 'API Key', placeholder: "sk-… or 'local'", secret: true },
      { key: 'token_alpha', label: 'α — prefill weight', placeholder: '0.2', numeric: true },
      { key: 'token_beta', label: 'β — decode weight', placeholder: '1.0', numeric: true },
    ],
  },
  {
    id: 'embedding', x: 58, y: 48, icon: 'network', name: 'Embedding', sources: ['agent', 'worker'],
    tag: 'bge-m3 vectors. The worker indexes documents with it and the research search queries with it: '
      + 'both must use the same model.',
    status: (c) => (c.embedding_url ? 'set' : 'inherits'),
    fields: [
      { key: 'embedding_model', label: 'Model Name', placeholder: 'BAAI/bge-m3' },
      { key: 'embedding_url', label: 'Base URL', placeholder: 'inherits the default model' },
      { key: 'embedding_api_key', label: 'API Key', placeholder: 'inherits the default model', secret: true },
    ],
  },
  {
    id: 'rerank', x: 58, y: 14, icon: 'sort', name: 'Reranker', sources: ['agent'],
    tag: 'Reorders the research search results (a reranker such as Qwen3-Reranker behind a /rerank endpoint, not the chat '
      + 'base URL). Empty URL = off: fusion order only.',
    status: (c) => (c.rerank_url ? 'set' : 'off'),
    fields: [
      { key: 'rerank_model', label: 'Model Name', placeholder: 'Qwen/Qwen3-Reranker-0.6B' },
      { key: 'rerank_url', label: 'Rerank URL', placeholder: 'http://reranker:8000/rerank (empty = off)' },
      { key: 'rerank_api_key', label: 'API Key', placeholder: 'inherits the default model', secret: true },
    ],
  },
  {
    id: 'mineru', x: 34, y: 82, icon: 'file', name: 'MinerU (PDF)', sources: ['worker'],
    tag: 'Reads each page of an uploaded PDF (layout, text, tables, formulas) through a MinerU vision '
      + 'server. Only the server and its key can change here, without a restart; the MinerU client, its local models '
      + 'and mineru.json are part of the worker deployment.',
    status: (c) => (c.mineru_server_url ? 'set' : c.features?.pdf_documents ? 'deployment' : 'off'),
    shows: (c) => c.mineru_server_url || c.mineru_default_url,
    fields: [
      { key: 'mineru_server_url', label: 'MinerU server URL', placeholder: 'empty = the deployment setting',
        hint: (c) => (c.mineru_default_url ? `Deployment setting: ${c.mineru_default_url}` : 'No deployment setting.') },
      { key: 'mineru_api_key', label: 'API Key', placeholder: 'inherits the default model', secret: true },
    ],
  },
];

const NODE_STATUS_META = {
  live: { chip: 'default', cls: 'bg-cyan-muted text-cyan' },
  set: { chip: 'own setting', cls: 'bg-success-muted text-success' },
  inherits: { chip: 'inherits', cls: 'bg-gold-muted text-gold' },
  deployment: { chip: 'deployment', cls: 'bg-success/10 text-success' },
  off: { chip: 'off', cls: 'bg-ink-surface text-cream-muted' },
};

function MapEdges({ config }) {
  const byId = Object.fromEntries([...MAP_SOURCES, ...MAP_NODES].map((s) => [s.id, s]));
  const fallback = byId.chat;
  return (
    <svg className="absolute inset-0 h-full w-full" viewBox="0 0 100 100"
      preserveAspectRatio="none" aria-hidden="true">
      {MAP_NODES.flatMap((n) => n.sources.map((sourceId) => {
        const s = byId[sourceId];
        const st = n.status(config);
        return (
          <line key={`call-${sourceId}-${n.id}`} x1={s.x} y1={s.y} x2={n.x} y2={n.y}
            className={st === 'off' ? 'stroke-cream-muted/30' : 'stroke-cyan/40'}
            strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
        );
      }))}
      {MAP_NODES.filter((n) => n.status(config) === 'inherits').map((n) => (
        <line key={`fb-${n.id}`} x1={n.x} y1={n.y} x2={fallback.x} y2={fallback.y}
          className="stroke-gold/60"
          strokeWidth="1.5" strokeDasharray="5 4" vectorEffect="non-scaling-stroke" />
      ))}
    </svg>
  );
}

function MapNode({ node, config, selected, onSelect }) {
  const st = node.status(config);
  const meta = NODE_STATUS_META[st];
  const model = node.shows ? node.shows(config) : config[node.fields[0].key];
  return (
    <button type="button" onClick={onSelect}
      style={{ left: `${node.x}%`, top: `${node.y}%` }}
      className={`absolute w-36 -translate-x-1/2 -translate-y-1/2 rounded-xl border p-2 text-left transition-all cursor-pointer bg-ink-raised hover:border-cyan/50 ${selected
        ? 'border-cyan ring-2 ring-cyan/30 shadow-glow'
        : 'border-border-default'}`}>
      <div className="flex items-center justify-between gap-1">
        <span className="flex min-w-0 items-center gap-1.5">
          <Icon name={node.icon} className="w-4 h-4 shrink-0 text-cyan" />
          <span className="truncate text-xs font-semibold text-cream">{node.name}</span>
        </span>
        {node.warnEmpty && st === 'inherits' ? (
          <span className="text-danger" title={node.warnEmpty}><Icon name="warning" className="w-3.5 h-3.5" /></span>
        ) : null}
      </div>
      <p className="mt-1 truncate font-mono text-[10px] text-cream-muted">
        {model || (st === 'off' ? '—' : 'default model')}
      </p>
      <span className={`mt-1 inline-block rounded px-1 py-px text-[9px] font-medium ${meta.cls}`}>
        {meta.chip}
      </span>
    </button>
  );
}

function MapSource({ source }) {
  return (
    <div style={{ left: `${source.x}%`, top: `${source.y}%` }}
      className="absolute w-28 -translate-x-1/2 -translate-y-1/2 rounded-lg border border-dashed border-border-subtle bg-ink-deep/60 p-2 text-center">
      <p className="text-xs font-semibold text-cream-secondary">{source.label}</p>
      <p className="text-[10px] text-cream-muted">{source.sub}</p>
    </div>
  );
}

function NodePanel({ node, config, set, onSave, saving }) {
  const st = node.status(config);
  return (
    <div className="space-y-3 rounded-xl border border-border-default bg-ink-deep/30 p-4">
      <div>
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-cream">
          <Icon name={node.icon} className="w-4 h-4 text-cyan" />{node.name}
        </h3>
        <p className="mt-1 text-xs leading-relaxed text-cream-muted">{node.tag}</p>
      </div>
      {node.fields.map((f, i) => {
        if (f.secret) {
          return <SecretField key={f.key} label={f.label} value={config[f.key]}
            onChange={set(f.key)} placeholder={f.placeholder} />;
        }
        const limit = f.limit ? config.research_limits?.[f.key] : null;
        const field = <Field key={f.key} value={config[f.key]} onChange={set(f.key)}
          label={limit ? `${f.label} · ${limit.min}–${limit.max}` : f.label}
          placeholder={limit ? `default ${limit.default}` : f.placeholder} />;
        if (f.hint) {
          return (
            <div key={f.key} className="space-y-1">
              {field}
              <p className="text-[11px] text-cream-muted">{f.hint(config)}</p>
            </div>
          );
        }
        // A heading before the first research limit.
        return f.limit && !node.fields[i - 1]?.limit ? (
          <div key={f.key} className="space-y-3">
            <div className="border-t border-border-subtle pt-3">
              <p className="text-xs font-semibold text-cream">Research limits, per student question</p>
              <p className="mt-1 text-xs leading-relaxed text-cream-muted">
                Empty uses the default. The time between research and total is left for the
                answer: keep it at least one model call long.
              </p>
            </div>
            {field}
          </div>
        ) : field;
      })}
      {node.warnEmpty && st === 'inherits' ? (
        <p className="text-xs text-gold"><Icon name="warning" className="inline-block w-3.5 h-3.5 align-[-2px]" /> {node.warnEmpty}</p>
      ) : null}
      <button type="button" onClick={onSave} disabled={saving}
        className="w-full rounded-lg gradient-cyan py-2 text-sm font-semibold text-on-accent transition-all hover:brightness-110 disabled:opacity-40">
        {saving ? 'Saving…' : `Save ${node.name}`}
      </button>
    </div>
  );
}

export default function LLMConfigTab() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [selectedId, setSelectedId] = useState('agent');
  const [loadError, setLoadError] = useState('');

  const loadConfig = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      setConfig(await api.get('/api/admin/llm/config'));
    } catch (err) {
      setLoadError(err.message || 'Could not load model settings.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadConfig(); }, [loadConfig]);

  const set = (k) => (v) => setConfig((p) => ({ ...p, [k]: v }));
  const selected = MAP_NODES.find((n) => n.id === selectedId);

  const saveNode = async (node) => {
    setSaving(true);
    try {
      // Per-node partial save: only this slot's keys (+ the base trio the
      // request contract expects). Token weights are numeric in the API
      // contract; an empty/invalid field sends null so a blank input never
      // silently zeroes a cost weight — the backend's partial upsert keeps
      // the existing value instead.
      const numOrNull = (v) => {
        const n = Number(v);
        return v === '' || v === null || Number.isNaN(n) ? null : n;
      };
      const payload = {
        base_url: config.base_url, api_key: config.api_key, model: config.model,
      };
      for (const f of node.fields) {
        payload[f.key] = f.numeric ? numOrNull(config[f.key]) : config[f.key];
      }
      const updated = await api.put('/api/admin/llm/config', payload);
      setConfig(updated);
      toast.success(`${node.name} saved`);
    } catch (err) { toast.error(err.message); } finally { setSaving(false); }
  };

  if (loading) return <Spinner />;
  if (loadError || !config) return (
    <div className="p-4 md:p-8" role="alert">
      <p className="text-danger">{loadError || 'No model settings returned.'}</p>
      <button type="button" onClick={loadConfig} className="mt-3 text-cyan hover:underline">
        Retry loading settings
      </button>
    </div>
  );

  return (
    <div className="p-4 md:p-8 overflow-y-auto h-full">
      <div className="max-w-6xl mx-auto">
        <PageHeader title="Model Routing Map"
          subtitle="Changes take effect immediately — no server restart required." />
        <p className="text-xs text-cream-muted mb-4">
          <span className="text-cyan">━━</span> calls ·{' '}
          <span className="text-gold">╌╌</span> empty slot uses the default model ·
          click a node to configure it.
          {config.features && (
            <span className="ml-2">
              Research agent: <span className="text-cream-secondary">{config.features.research_agent ? 'on' : 'off'}</span>{' · '}
              PDF documents: <span className="text-cream-secondary">{config.features.pdf_documents ? 'on' : 'off'}</span>
              <span> (deployment settings)</span>
            </span>
          )}
        </p>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
          {/* Narrow screens keep the map at full size and scroll it sideways. */}
          <div className="min-w-0">
            <div className="overflow-x-auto rounded-xl border border-border-default bg-ink-deep/40">
              <div className="relative h-[470px] min-w-[720px] overflow-hidden">
                <MapEdges config={config} />
                {MAP_SOURCES.map((s) => <MapSource key={s.id} source={s} />)}
                {MAP_NODES.map((n) => (
                  <MapNode key={n.id} node={n} config={config}
                    selected={n.id === selectedId} onSelect={() => setSelectedId(n.id)} />
                ))}
              </div>
            </div>
            <p className="mt-1.5 text-[11px] text-cream-muted md:hidden">Swipe sideways to see the whole map.</p>
          </div>

          <NodePanel node={selected} config={config} set={set}
            onSave={() => saveNode(selected)} saving={saving} />
        </div>
        <ServersPanel version={config} />
      </div>
    </div>
  );
}

const TONE = { ok: 'text-success', bad: 'text-danger', warn: 'text-gold', muted: 'text-cream-muted' };

// What each slot's server really serves: a model name the server does not
// have makes every call fail, and the context length is not set here.
function ServersPanel({ version }) {
  const [rows, setRows] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    setBusy(true);
    try {
      setRows((await api.get('/api/admin/llm/servers')).servers);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  }, []);
  useEffect(() => { load(); }, [load, version]);
  return (
    <section className="mt-6 rounded-xl border border-border-default bg-ink-raised p-4">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-cream">What the servers serve</h3>
        <button type="button" onClick={load} disabled={busy}
          className="text-xs text-cyan hover:underline disabled:opacity-50">{busy ? 'Checking…' : 'Check again'}</button>
      </div>
      <p className="mb-3 text-xs text-cream-muted">
        Asked from each server now. The context length is set when the model server starts (vLLM --max-model-len),
        not on this page.
      </p>
      {!rows ? <p className="text-xs text-cream-muted">Checking…</p> : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-xs">
            <thead>
              <tr className="border-b border-border-subtle text-left text-cream-muted">
                <th className="px-2 py-1.5 font-medium">Slot</th>
                <th className="px-2 py-1.5 font-medium">Model</th>
                <th className="px-2 py-1.5 font-medium">Server</th>
                <th className="px-2 py-1.5 font-medium">State</th>
                <th className="px-2 py-1.5 font-medium">Context length</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const state = serverState(r);
                return (
                  <tr key={r.slot} className="border-b border-border-subtle last:border-0">
                    <td className="px-2 py-1.5 text-cream">{r.label}</td>
                    <td className="px-2 py-1.5 font-mono text-cream-secondary">{r.model || '—'}</td>
                    <td className="px-2 py-1.5 font-mono text-cream-muted">{r.server || '—'}</td>
                    <td className={`px-2 py-1.5 ${TONE[state.tone]}`}>{state.text}</td>
                    <td className="px-2 py-1.5 font-mono text-cream-secondary">{contextLength(r.max_model_len)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
