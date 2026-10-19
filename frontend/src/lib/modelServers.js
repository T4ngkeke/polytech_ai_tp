/**
 * The "What the servers serve" table of the admin LLM Config tab.
 * Rows come from GET /api/admin/llm/servers.
 */

/** { text, tone } for a slot's state; tone is ok, bad, warn or muted. */
export function serverState(row) {
  if (row.status === 'ok') return { text: 'served', tone: 'ok' };
  if (row.status === 'off') return { text: 'off', tone: 'muted' };
  if (row.status === 'unreachable') return { text: 'server does not answer', tone: 'warn' };
  const served = row.served?.length ? row.served.join(', ') : 'nothing';
  return { text: `not served — the server has: ${served}`, tone: 'bad' };
}

/** "65,536 tokens (64k)"; '—' when the server does not say. */
export function contextLength(tokens) {
  if (!tokens) return '—';
  return `${tokens.toLocaleString('en-US')} tokens (${Math.round(tokens / 1024)}k)`;
}
