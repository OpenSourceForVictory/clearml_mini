import argparse,json,os
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from common import load

parser=argparse.ArgumentParser();parser.add_argument('--model',required=True)
args=parser.parse_args();config=load()
assert args.model==config['business']['model']
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path=='/healthz' else 404);self.end_headers();self.wfile.write(b'{}')
    def do_POST(self):
        size=int(self.headers.get('Content-Length','0'))
        if not 0<size<=65536:self.send_error(400);return
        received=json.loads(self.rfile.read(size))
        value={'received':received,'model':args.model,'server_execution':config['platform']['execution_id'],'server_label':os.environ['DEMO_LABEL']}
        self.send_response(200);self.end_headers();self.wfile.write(json.dumps(value).encode())
listen=config['platform']['listen']
ThreadingHTTPServer((listen['address'],listen['port']),Handler).serve_forever()
