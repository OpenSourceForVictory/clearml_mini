"""Disposable HTTP inference stub for contract acceptance, not a model server."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 65536:
                self.send_error(413); return
            body = json.loads(self.rfile.read(size))
            if body.get('task_name') == 'fail':
                self.send_error(503, 'intentional test failure'); return
            if body.get('task_name') == 'slow':
                time.sleep(5)
            obs = body['observation']
            action = (obs['target'] > obs['position']) - (obs['target'] < obs['position'])
            data = json.dumps({'action': action}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
        except (ValueError, KeyError, TypeError):
            self.send_error(400)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format, *args):
        pass


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=19090)
    a = p.parse_args()
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
