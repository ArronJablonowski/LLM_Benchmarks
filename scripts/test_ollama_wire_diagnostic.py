import http.client
import http.server
import json
from pathlib import Path
import tempfile
import threading
import unittest
from ollama_wire_diagnostic import Capture, make_server


class WireTests(unittest.TestCase):
    def test_capture_limit_is_storage_only(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'wire';c=Capture(p,5);c.write(b'abc');c.write(b'defgh');s=c.close()
            self.assertEqual(p.read_bytes(),b'abcde')
            self.assertEqual((s['bytes_received'],s['bytes_saved'],s['captured_prefix_only']),(8,5,True))
            with self.assertRaises(FileExistsError):Capture(p)
    def test_relay_preserves_body_and_does_not_record_request_secrets(self):
        received=[]
        wire=b'{"message":{"content":"hello"},"done":false}\n{"done":true}\n'
        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                received.append(self.rfile.read(int(self.headers['Content-Length'])))
                self.send_response(200);self.send_header('Content-Type','application/x-ndjson');self.end_headers()
                self.wfile.write(wire)
        upstream=http.server.ThreadingHTTPServer(('127.0.0.1',0),Upstream)
        threading.Thread(target=upstream.serve_forever,daemon=True).start()
        with tempfile.TemporaryDirectory() as d:
            relay=make_server(d,upstream_port=upstream.server_port)
            threading.Thread(target=relay.serve_forever,daemon=True).start()
            try:
                body=json.dumps({'model':'glm-ocr:latest','stream':True,'messages':[{'role':'user','content':'private-marker'}]})
                c=http.client.HTTPConnection('127.0.0.1',relay.server_port,timeout=5)
                c.request('POST','/api/chat',body,{'Authorization':'secret-marker'})
                r=c.getresponse();self.assertEqual(r.status,200);self.assertEqual(r.read(),wire);c.close()
                self.assertEqual(received,[body.encode()])
                # Waiting for the connection EOF also waits for receipt creation.
                relay.shutdown();relay.server_close()
                self.assertEqual((Path(d)/'wire-0001.bin').read_bytes(),wire)
                metadata=(Path(d)/'wire-0001.json').read_text()
                self.assertNotIn('private-marker',metadata);self.assertNotIn('secret-marker',metadata)
            finally:
                relay.shutdown();relay.server_close()
        upstream.shutdown();upstream.server_close()
    def test_rejects_foreign_model_before_upstream(self):
        with tempfile.TemporaryDirectory() as d:
            server=make_server(d,upstream_port=1)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            try:
                c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
                c.request('POST','/api/chat',json.dumps({'model':'external'}));r=c.getresponse();self.assertEqual(r.status,403);r.read();c.close()
                self.assertEqual(list(Path(d).iterdir()),[])
            finally:server.shutdown();server.server_close()


if __name__=='__main__':unittest.main()
