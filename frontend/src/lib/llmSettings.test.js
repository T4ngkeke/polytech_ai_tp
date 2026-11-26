import { test } from 'node:test';
import assert from 'node:assert/strict';

import { changes, contextShare, fromConfig, payload, testState, toFields, unusedServers, usedFor } from './llmSettings.js';

// The release settings on 2026-11-19 (fake key): every chat slot written out
// explicitly with the main model's values, other keys empty.
const RELEASE = {
  base_url: 'http://m4-flashnext-v030:8000/v1', api_key: 'K', model: 'flash-next',
  agent_base_url: 'http://m4-flashnext-v030:8000/v1', agent_api_key: '', agent_model: 'flash-next',
  vlm_base_url: 'http://m4-flashnext-v030:8000/v1', vlm_api_key: '', vlm_model: 'flash-next',
  embedding_url: 'http://edullm-embeddings:8000/v1', embedding_api_key: '', embedding_model: 'BAAI/bge-m3',
  rerank_url: 'http://edullm-reranker:8000/rerank', rerank_api_key: '', rerank_model: 'Qwen/Qwen3-Reranker-0.6B',
  rerank_enabled: '', mineru_server_url: '', mineru_api_key: '', mineru_default_url: 'http://edullm-mineru-vlm:8000/v1',
  picture_captions: 'on', picture_caption_priority: '10', picture_caption_budget_seconds: '',
  agent_max_tool_calls: '', history_context_percent: '40', token_alpha: 0.2, token_beta: 1,
};

test('the release settings read as four servers, all features on the main model', () => {
  const st = fromConfig(RELEASE);
  assert.deepEqual(st.servers.map((s) => s.id), ['main', 'embed', 'rerank', 'pdf']);
  assert.deepEqual(st.use, { answers: 'main', vision: 'main' });
  assert.deepEqual(st.on, { vision: true, rerank: true, captions: true });
  assert.equal(st.key, 'K');
  assert.deepEqual(st.num, { picture_caption_priority: 10, history_context_percent: 40 });
  assert.equal(st.servers.find((s) => s.id === 'pdf').defaultUrl, 'http://edullm-mineru-vlm:8000/v1');
});

test('an unchanged page saves nothing that changes behaviour', () => {
  const body = payload(RELEASE, fromConfig(RELEASE));
  // Only the explicit copies of the main model are written as "same as main" (empty).
  assert.deepEqual(body, { agent_base_url: '', agent_model: '', vlm_base_url: '' });
  assert.deepEqual(changes(fromConfig(RELEASE), fromConfig(RELEASE)), []);
});

test('changing the main model also moves the features that use it', () => {
  const st = fromConfig(RELEASE);
  st.servers[0] = { ...st.servers[0], model: 'flash-next-2' };
  const f = toFields(st);
  assert.equal(f.model, 'flash-next-2');
  assert.equal(f.agent_model, '');          // student answers follow the main model
  assert.equal(f.vlm_model, 'flash-next-2'); // vision on: its model must be named
  assert.deepEqual(changes(fromConfig(RELEASE), st), ['Main model: model name']);
});

test('a second chat model for student answers is stored in the agent slot with its own key', () => {
  const st = fromConfig(RELEASE);
  st.servers.push({ id: 's2', kind: 'chat', name: 'Second chat model', url: 'https://other/v1', model: 'm2', ownKey: true, key: 'K2' });
  assert.equal(unusedServers(st).length, 1);
  st.use.answers = 's2';
  assert.deepEqual(unusedServers(st), []);
  const body = payload(RELEASE, st);
  assert.deepEqual([body.agent_base_url, body.agent_model, body.agent_api_key], ['https://other/v1', 'm2', 'K2']);
  // Read back, it is a server of its own again.
  const back = fromConfig({ ...RELEASE, ...body });
  assert.equal(back.use.answers, 'agent');
  assert.deepEqual(usedFor(back, 'agent'), ['student answers']);
  assert.deepEqual(usedFor(back, 'main'), ['image views']);
});

test('switches: vision off empties its model, the reranker keeps its address', () => {
  const st = fromConfig(RELEASE);
  st.on = { ...st.on, vision: false, rerank: false, captions: false };
  const body = payload(RELEASE, st);
  assert.equal(body.vlm_model, '');
  assert.equal(body.rerank_enabled, 'off');
  assert.equal(body.rerank_url, undefined);
  assert.equal(body.picture_captions, 'off');
  const back = fromConfig({ ...RELEASE, ...body });
  assert.deepEqual(back.on, { vision: false, rerank: false, captions: false });
  assert.equal(back.servers.find((s) => s.id === 'rerank').url, 'http://edullm-reranker:8000/rerank');
});

test('a key only differs from the shared one when the server has its own', () => {
  const stored = { ...RELEASE, embedding_api_key: 'K' };
  const st = fromConfig(stored);   // same as shared: not its own
  assert.equal(st.servers.find((s) => s.id === 'embed').ownKey, false);
  const own = fromConfig({ ...RELEASE, mineru_api_key: 'M' });
  assert.equal(own.servers.find((s) => s.id === 'pdf').ownKey, true);
  st.key = 'NEW';
  // The copy of the old shared key is emptied, so the embeddings follow the new key.
  assert.deepEqual(payload(stored, st), { agent_base_url: '', agent_model: '', vlm_base_url: '', api_key: 'NEW', embedding_api_key: '' });
});

test('numbers back to the default are stored empty', () => {
  const st = fromConfig(RELEASE);
  delete st.num.history_context_percent;
  st.num.agent_max_tool_calls = 20;
  const body = payload(RELEASE, st);
  assert.equal(body.history_context_percent, '');
  assert.equal(body.agent_max_tool_calls, '20');
  assert.deepEqual(changes(fromConfig(RELEASE), st), ['Chat history', 'Course tool calls']);
});

test('context shares and test results in words', () => {
  assert.deepEqual(contextShare(131072, 30), { tokens: 39321, chars: 117963 });
  assert.equal(testState({ status: 'refused' }).text, 'Key refused (401)');
  assert.equal(testState({ status: 'ok', max_model_len: 131072 }).text, 'Works · 128k');
  assert.equal(testState({ status: 'model_missing', served: ['flash-next'] }).text, 'Model not served — it has flash-next');
  assert.equal(testState({ status: 'unreachable' }).tone, 'warn');
});
