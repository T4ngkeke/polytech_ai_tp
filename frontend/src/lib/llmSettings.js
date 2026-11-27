/**
 * The admin "Model settings" page (pages/admin/LlmConfigTab.jsx) shows the
 * stored model settings as servers and features. This file turns the stored
 * settings (GET /api/admin/llm/config) into that page state, and the page state
 * back into the fields that PUT /api/admin/llm/config takes. No React here.
 *
 * Storage (backend/app/services/model_routing.py) has one slot per use: the
 * main model, the agent (student answers), vision, embeddings, reranker, and
 * the MinerU server. An empty URL, model or key of the agent and vision slots
 * means "the main model's"; an empty vision model means vision is off. The
 * page shows one shared key (the main model's), the servers behind the slots,
 * and which server each feature calls.
 */

export const SERVER_KINDS = { chat: 'Chat model', embed: 'Embeddings', rerank: 'Reranker', pdf: 'PDF reader' };

// Number settings: stored field, label, help, unit, default and range, as the backend
// has them (services/research_service.ResearchLimits and LIMIT_KEYS, worker/picture_captions).
// The page uses the backend's research_limits when it sends them.
export const NUMBERS = [
  { key: 'history_context_percent', group: 'context', name: 'Chat history', unit: '%', def: 30, min: 0, max: 50,
    help: 'Earlier messages of the conversation. Past the limit the oldest turns go, several at once.' },
  { key: 'agent_max_tool_calls', group: 'research', name: 'Course tool calls', unit: '', def: 30, min: 1, max: 40,
    help: 'Searches, page reads and greps in one answer.' },
  { key: 'agent_max_visual_calls', group: 'research', name: 'Image views', unit: '', def: 4, min: 0, max: 10,
    help: 'Pictures the model looks at in one answer.' },
  { key: 'agent_research_seconds', group: 'research', name: 'Research time', unit: 's', def: 300, min: 5, max: 1800,
    help: 'After this the tools are withdrawn and the model answers from what it read.' },
  { key: 'agent_total_seconds', group: 'research', name: 'Total time, answer included', unit: 's', def: 420, min: 10, max: 1800,
    help: 'Keep it at least one model call longer than the research time.' },
  { key: 'agent_call_seconds', group: 'research', name: 'One model call', unit: 's', def: 120, min: 5, max: 600,
    help: 'A single call that takes longer is stopped.' },
  { key: 'agent_max_input_tokens', group: 'research', name: 'Input tokens per call', unit: 'tokens', def: 36000, min: 4000, max: 200000,
    help: 'Room for course pages in one call, on top of the history.' },
  { key: 'picture_caption_priority', group: 'pdf', name: 'Caption priority', unit: '', def: null, min: -1000, max: 1000,
    help: 'Students (0) go first when this is higher. Needs priority scheduling on the model server; empty sends none.' },
  { key: 'picture_caption_budget_seconds', group: 'pdf', name: 'Caption time per document', unit: 's', def: 900, min: 60, max: 7200,
    help: 'Pictures not described in this time have no caption; the document is not held back.' },
];

/** The NUMBERS entry of a field, with the backend's own range and default when it sends them. */
export function numberSpec(key, config) {
  const base = NUMBERS.find((n) => n.key === key);
  const backend = config?.research_limits?.[key];
  return backend ? { ...base, def: backend.default, min: backend.min, max: backend.max } : base;
}

const same = (a, b) => a.url === b.url && a.model === b.model && (a.ownKey ? a.key : '') === (b.ownKey ? b.key : '');

/** Page state from the stored settings. */
export function fromConfig(c) {
  const key = c.api_key || '';
  const main = { id: 'main', kind: 'chat', name: 'Main model', url: c.base_url || '', model: c.model || '', ownKey: false, key: '' };
  const servers = [main];
  // A chat slot that is not the main model becomes a server of its own.
  const chatServer = (id, name, url, model, slotKey) => {
    const s = { id, kind: 'chat', name, url: url || main.url, model: model || main.model,
      ownKey: Boolean(slotKey) && slotKey !== key, key: slotKey && slotKey !== key ? slotKey : '' };
    if (same(s, main)) return 'main';
    const found = servers.find((x) => same(x, s));
    if (found) return found.id;
    servers.push(s);
    return s.id;
  };
  const answers = chatServer('agent', 'Answer model', c.agent_base_url, c.agent_model, c.agent_api_key);
  const visionOn = Boolean(c.vlm_model);
  const vision = visionOn ? chatServer('vision', 'Image model', c.vlm_base_url, c.vlm_model, c.vlm_api_key) : 'main';
  const own = (slotKey) => ({ ownKey: Boolean(slotKey) && slotKey !== key, key: slotKey && slotKey !== key ? slotKey : '' });
  servers.push(
    { id: 'embed', kind: 'embed', name: 'Embeddings', url: c.embedding_url || main.url, model: c.embedding_model || '', ...own(c.embedding_api_key) },
    { id: 'rerank', kind: 'rerank', name: 'Reranker', url: c.rerank_url || '', model: c.rerank_model || '', ...own(c.rerank_api_key) },
    { id: 'pdf', kind: 'pdf', name: 'PDF reader (MinerU)', url: c.mineru_server_url || '', model: '', ...own(c.mineru_api_key),
      defaultUrl: c.mineru_default_url || '' },
  );
  const num = {};
  for (const n of NUMBERS) {
    const v = c[n.key];
    if (v !== undefined && v !== null && String(v).trim() !== '') num[n.key] = Number(v);
  }
  return {
    key, servers,
    use: { answers, vision },
    on: { vision: visionOn, rerank: Boolean(c.rerank_url) && c.rerank_enabled !== 'off', captions: c.picture_captions === 'on' },
    num, alpha: Number(c.token_alpha ?? 0.2), beta: Number(c.token_beta ?? 1),
  };
}

const keyOf = (s) => (s.ownKey ? s.key : '');

/** The stored fields that stand for a page state (every field the page owns). */
export function toFields(st) {
  const byId = Object.fromEntries(st.servers.map((s) => [s.id, s]));
  const main = byId.main;
  const slot = (id) => (id === 'main' || !byId[id]
    ? { url: '', model: '', key: '' } : { url: byId[id].url, model: byId[id].model, key: keyOf(byId[id]) });
  const agent = slot(st.use.answers);
  const vision = slot(st.use.vision);
  const visionModel = !st.on.vision ? '' : (st.use.vision === 'main' || !byId[st.use.vision] ? main.model : vision.model);
  const out = {
    base_url: main.url, model: main.model, api_key: st.key,
    agent_base_url: agent.url, agent_model: agent.model, agent_api_key: agent.key,
    vlm_base_url: st.on.vision ? vision.url : '', vlm_model: visionModel, vlm_api_key: st.on.vision ? vision.key : '',
    embedding_url: byId.embed.url, embedding_model: byId.embed.model, embedding_api_key: keyOf(byId.embed),
    rerank_url: byId.rerank.url, rerank_model: byId.rerank.model, rerank_api_key: keyOf(byId.rerank),
    rerank_enabled: st.on.rerank ? 'on' : 'off',
    mineru_server_url: byId.pdf.url, mineru_api_key: keyOf(byId.pdf),
    picture_captions: st.on.captions ? 'on' : 'off',
    token_alpha: st.alpha, token_beta: st.beta,
  };
  for (const n of NUMBERS) out[n.key] = st.num[n.key] === undefined ? '' : String(st.num[n.key]);
  return out;
}

/** The PUT body: only the fields whose stored value differs from the page. */
export function payload(config, st) {
  const fields = toFields(st);
  const body = {};
  for (const [k, v] of Object.entries(fields)) {
    const stored = config[k];
    const differs = typeof v === 'number' ? Number(stored) !== v : String(stored ?? '') !== v;
    if (differs) body[k] = v;
  }
  // "off" is the same as no reranker address; do not write it for nothing.
  if (body.rerank_enabled === 'on' && !config.rerank_enabled && config.rerank_url) delete body.rerank_enabled;
  return body;
}

/** Servers no feature uses: a chat server that is not the main model is kept only while a feature calls it. */
export function unusedServers(st) {
  return st.servers.filter((s) => s.kind === 'chat' && s.id !== 'main'
    && st.use.answers !== s.id && !(st.on.vision && st.use.vision === s.id));
}

/** What each server is used for, in words. */
export function usedFor(st, id) {
  const out = [];
  if (st.use.answers === id) out.push('student answers');
  if (st.on.vision && st.use.vision === id) out.push('image views');
  if (id === 'embed') out.push('embeddings');
  if (id === 'rerank' && st.on.rerank) out.push('reranking');
  if (id === 'pdf') out.push('PDF reading');
  return out;
}

/** The key a server sends: its own, or the shared one. */
export const serverKey = (st, s) => (s.ownKey ? s.key : st.key);

/** The address a server is called at (MinerU: the deployment's when empty). */
export const serverUrl = (s) => s.url || s.defaultUrl || '';

/** The changes between two page states, in words, for the save bar. */
export function changes(a, b) {
  const out = [];
  if (a.key !== b.key) out.push('Shared key');
  const ids = [...new Set([...a.servers.map((s) => s.id), ...b.servers.map((s) => s.id)])];
  for (const id of ids) {
    const x = a.servers.find((s) => s.id === id);
    const y = b.servers.find((s) => s.id === id);
    if (!x) { out.push(`New server: ${y.name || 'unnamed'}`); continue; }
    if (!y) { out.push(`Removed server: ${x.name}`); continue; }
    if (x.name !== y.name) out.push(`${y.name}: name`);
    if (x.model !== y.model) out.push(`${y.name}: model name`);
    if (x.url !== y.url) out.push(`${y.name}: address`);
    if (x.ownKey !== y.ownKey || (y.ownKey && x.key !== y.key)) out.push(`${y.name}: own key`);
  }
  if (a.use.answers !== b.use.answers) out.push('Student answers: server');
  if (a.use.vision !== b.use.vision) out.push('Image views: server');
  const onNames = { vision: 'Image views', rerank: 'Reranking', captions: 'Picture captions' };
  for (const k of Object.keys(onNames)) if (a.on[k] !== b.on[k]) out.push(`${onNames[k]}: ${b.on[k] ? 'on' : 'off'}`);
  for (const n of NUMBERS) if (a.num[n.key] !== b.num[n.key]) out.push(n.name);
  if (a.alpha !== b.alpha) out.push('Billing α');
  if (a.beta !== b.beta) out.push('Billing β');
  return out;
}

/** Tokens and characters that a share of the context stands for (3 characters per token, as the chat counts). */
export function contextShare(contextTokens, percent) {
  const tokens = Math.floor(contextTokens * percent / 100);
  return { tokens, chars: tokens * 3 };
}

/** A server test result in words, and its tone (ok, bad, warn, muted). */
export function testState(result) {
  if (!result) return { text: 'Not tested', tone: 'muted' };
  if (result.status === 'testing') return { text: 'Testing…', tone: 'muted' };
  if (result.status === 'changed') return { text: 'Changed · test it', tone: 'accent' };
  if (result.status === 'ok') {
    const ctx = result.max_model_len ? ` · ${Math.round(result.max_model_len / 1024)}k` : '';
    return { text: `Works${ctx}`, tone: 'ok' };
  }
  if (result.status === 'refused') return { text: 'Key refused (401)', tone: 'bad' };
  if (result.status === 'invalid_url') return { text: 'Not an http(s) address', tone: 'bad' };
  if (result.status === 'model_missing') {
    const served = result.served?.length ? result.served.join(', ') : 'nothing';
    return { text: `Model not served — it has ${served}`, tone: 'bad' };
  }
  return { text: 'No answer from this address', tone: 'warn' };
}
