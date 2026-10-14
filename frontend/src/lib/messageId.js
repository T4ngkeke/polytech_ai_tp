// IDs for pending UI messages only; database IDs are assigned by the server.
// A page-local counter works on ordinary HTTP as well as HTTPS/localhost.
let sequence = 0;

export function createMessageId() {
  return `local-message-${Date.now()}-${sequence++}`;
}

// A random UUID (v4) for a request the server may see twice (client_request_id).
// crypto.randomUUID exists only in secure contexts (HTTPS, localhost); over
// plain HTTP, such as the Tailscale address, crypto.getRandomValues still works.
export function createRequestId(cryptoApi = globalThis.crypto) {
  if (typeof cryptoApi.randomUUID === 'function') return cryptoApi.randomUUID();
  const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 4122 variant
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
