"""Key check for the vLLM model servers (documentation/MODEL_SERVERS.md).

Every request must carry `Authorization: Bearer <key>`, except the paths in
OPEN_PATHS (health checks and the Prometheus metrics). vLLM's own --api-key
is not enough: it checks only paths under /v1, /v2, /inference and /cohere, so
/invocations (a full chat or embedding call), /rerank, /score, /pooling and
/tokenize stay open to anyone who reaches the port.

vLLM loads this class with `--middleware edullm_key_check.KeyCheck`. The file
is mounted read-only into the server's site-packages, and the key is in the
environment variable EDULLM_MODEL_API_KEY. A server started without the key
stops at start instead of running open.

Standard library only: it runs inside the vLLM images, not in the app.
"""
import hashlib
import hmac
import json
import os

KEY_VARIABLE = 'EDULLM_MODEL_API_KEY'
OPEN_PATHS = frozenset({'/health', '/ping', '/metrics', '/version'})


class KeyCheck:
    def __init__(self, app, key: str | None = None):
        key = (key if key is not None else os.environ.get(KEY_VARIABLE, '')).strip()
        if not key:
            raise RuntimeError(f'{KEY_VARIABLE} is not set: the model server would run without a key')
        self.app = app
        self._digest = hashlib.sha256(key.encode()).digest()

    def _allowed(self, scope) -> bool:
        path = scope.get('path', '').removeprefix(scope.get('root_path', ''))
        if path in OPEN_PATHS:
            return True
        for name, value in scope.get('headers', []):
            if name.lower() == b'authorization':
                scheme, _, token = value.decode('latin-1').partition(' ')
                return scheme.lower() == 'bearer' and hmac.compare_digest(
                    hashlib.sha256(token.strip().encode()).digest(), self._digest)
        return False

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket') or self._allowed(scope):
            return await self.app(scope, receive, send)
        if scope['type'] == 'websocket':
            await send({'type': 'websocket.close', 'code': 1008})
            return
        body = json.dumps({'error': 'Unauthorized'}).encode()
        await send({'type': 'http.response.start', 'status': 401,
                    'headers': [(b'content-type', b'application/json'),
                                (b'content-length', str(len(body)).encode()),
                                (b'www-authenticate', b'Bearer')]})
        await send({'type': 'http.response.body', 'body': body})
