"""HTTP echo service for platform integration, not a real inference model."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

config = json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == '/healthz' else 404)
        self.end_headers()
        self.wfile.write(b'{}')

    def do_POST(self):
        size = int(self.headers.get('Content-Length', '0'))
        if not 0 < size <= 65536:
            self.send_error(400)
            return
        received = json.loads(self.rfile.read(size))
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'received': received, 'model': config['business']['model']}).encode())

if __name__ == '__main__':
    listen = config['platform']['listen']
    ThreadingHTTPServer((listen['address'], listen['port']), Handler).serve_forever()
