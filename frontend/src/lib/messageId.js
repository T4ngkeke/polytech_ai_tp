// IDs for pending UI messages only; database IDs are assigned by the server.
// A page-local counter works on ordinary HTTP as well as HTTPS/localhost.
let sequence = 0;

export function createMessageId() {
  return `local-message-${Date.now()}-${sequence++}`;
}
