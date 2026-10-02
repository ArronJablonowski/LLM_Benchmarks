#!/usr/bin/env python3
"""Opt-in loopback relay for one isolated benchmark's bounded wire evidence.

Does not issue inference itself or modify bodies. Use a separate diagnostic
configuration pointing at this relay; never replace an active provider endpoint.
"""
import argparse
import hashlib
import http.client
import http.server
import json
from pathlib import Path
import threading

CAPTURE_LIMIT = 17 << 20
REQUEST_LIMIT = 1 << 20
HOP_HEADERS = {'connection', 'transfer-encoding', 'keep-alive', 'proxy-authenticate',
               'proxy-authorization', 'te', 'trailer', 'upgrade', 'host', 'content-length'}


class Capture:
    def __init__(self, path, limit=CAPTURE_LIMIT):
        self.file = path.open('xb')
        path.chmod(0o600)
        self.limit, self.total, self.saved = limit, 0, 0
        self.digest = hashlib.sha256()
    def write(self, data):
        self.total += len(data)
        self.digest.update(data)
        keep = data[:max(0, self.limit-self.saved)]
        self.file.write(keep)
        self.saved += len(keep)
    def close(self):
        self.file.close()
        return dict(bytes_received=self.total, bytes_saved=self.saved,
                    captured_prefix_only=self.total > self.saved, received_sha256=self.digest.hexdigest())


def make_server(directory, port=0, upstream_port=11434, model='glm-ocr:latest'):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock, sequence = threading.Lock(), [0]

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'
        def log_message(self, *args):
            pass  # Never log request paths, credentials, or bodies.
        def do_HEAD(self): self.relay()
        def do_GET(self): self.relay()
        def do_POST(self): self.relay()
        def relay(self):
            if self.path not in ('/', '/api/tags', '/api/ps', '/api/show', '/api/chat', '/api/generate'):
                self.send_error(404); return
            try:
                if self.headers.get('Transfer-Encoding'):
                    self.send_error(400); return
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= REQUEST_LIMIT:
                    self.send_error(413); return
                body = self.rfile.read(length)
                request = json.loads(body) if body else {}
            except (ValueError, json.JSONDecodeError):
                self.send_error(400); return
            if self.path in ('/api/chat', '/api/generate') and request.get('model') != model:
                self.send_error(403); return
            capture, metadata, name = None, {}, None
            if self.path == '/api/chat':
                with lock:
                    sequence[0] += 1
                    name = f'wire-{sequence[0]:04d}'
                capture = Capture(directory/(name+'.bin'))
                metadata = dict(model=request.get('model'), request_sha256=hashlib.sha256(body).hexdigest(),
                    request_bytes=len(body), options=request.get('options'), stream=request.get('stream'),
                    tool_names=[x.get('function', {}).get('name') for x in request.get('tools', [])])
            connection = http.client.HTTPConnection('127.0.0.1', upstream_port, timeout=310)
            try:
                headers = {k:v for k,v in self.headers.items() if k.lower() not in HOP_HEADERS}
                connection.request(self.command, self.path, body=body, headers=headers)
                response = connection.getresponse()
                metadata['http_status'] = response.status
                self.send_response(response.status)
                for key,value in response.getheaders():
                    if key.lower() not in HOP_HEADERS:
                        self.send_header(key, value)
                self.send_header('Connection', 'close')
                self.end_headers()
                while self.command != 'HEAD':
                    chunk = response.read1(65536)
                    if not chunk: break
                    if capture: capture.write(chunk)
                    self.wfile.write(chunk)
                    self.wfile.flush()
                metadata['relay_status'] = 'upstream_eof'
            except (BrokenPipeError, ConnectionResetError):
                metadata['relay_status'] = 'client_disconnected'
            except Exception as exc:
                metadata['relay_status'] = type(exc).__name__  # No sensitive error/body text.
            finally:
                connection.close()
                self.close_connection = True
                if capture:
                    metadata.update(capture.close())
                    path = directory/(name+'.json')
                    with path.open('x') as f: json.dump(metadata, f, indent=2)
                    path.chmod(0o600)
    return http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--model',default='glm-ocr:latest')
    args=parser.parse_args()
    server=make_server(args.directory,args.port,model=args.model)
    endpoint=f'http://127.0.0.1:{server.server_port}'
    (args.directory/'endpoint.json').write_text(json.dumps(dict(endpoint=endpoint)))
    print(endpoint,flush=True)
    server.serve_forever()


if __name__=='__main__':main()
