"""Loopback-only model proxy: retain costs, never prompts, images or model reasoning."""
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ALLOWED_UPSTREAMS = ('http://127.0.0.1:18001/v1',  # Ornith (worker, pictures, Marker --use_llm)
                     'http://127.0.0.1:18002/v1',  # surya VLM used by Marker
                     'http://127.0.0.1:18003/v1',  # MinerU2.5 VLM used by MinerU hybrid
                     'http://127.0.0.1:18004/v1')  # PaddleOCR-VL used by PaddleOCR


class ModelMeter:
    def __init__(self, upstream):
        if upstream not in ALLOWED_UPSTREAMS:
            raise ValueError('This offline experiment only targets localhost:18001-18004')
        self.events = []
        meter = self
        root = upstream[:-len('/v1')]

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                # Health and model-list checks are passed through and not counted as model calls.
                if self.path not in ('/health', '/v1/models'):
                    self.send_error(404); return
                try:
                    with urllib.request.urlopen(root+self.path, timeout=10) as response:
                        result, status = response.read(), response.status
                except urllib.error.HTTPError as exc:
                    result, status = exc.read(), exc.code
                except Exception as exc:
                    result, status = json.dumps({'error':str(exc)}).encode(), 502
                self.send_response(status)
                self.send_header('Content-Length',str(len(result)))
                self.end_headers()
                self.wfile.write(result)

            def do_POST(self):
                if self.path != '/v1/chat/completions':
                    self.send_error(404); return
                body = self.rfile.read(int(self.headers['Content-Length']))
                payload = json.loads(body)
                event = {'started': time.time(), 'model': payload.get('model'),
                         'temperature': payload.get('temperature'), 'max_tokens': payload.get('max_tokens')}
                meter.events.append(event)
                start = time.monotonic()
                try:
                    req = urllib.request.Request(upstream+'/chat/completions', data=body,
                                                 headers={'Content-Type':'application/json'})
                    with urllib.request.urlopen(req,timeout=180) as response:
                        result = response.read()
                        data = json.loads(result)
                    event['usage'] = data.get('usage')
                    event['finish_reasons'] = [c.get('finish_reason') for c in data.get('choices',[])]
                    event['output_chars'] = sum(len(c.get('message',{}).get('content') or '') for c in data.get('choices',[]))
                    self.send_response(200)
                except urllib.error.HTTPError as exc:
                    result = exc.read(); event['http_status'] = exc.code
                    self.send_response(exc.code)
                except Exception as exc:
                    result = json.dumps({'error':str(exc)}).encode()
                    event['error'] = str(exc); self.send_response(502)
                event['seconds'] = time.monotonic()-start
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(result)))
                self.end_headers()
                try:
                    self.wfile.write(result)
                except (BrokenPipeError, ConnectionResetError):
                    event['client_disconnected'] = True

        self.server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)

    def __enter__(self):
        self.thread.start(); return self

    def __exit__(self,*args):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
