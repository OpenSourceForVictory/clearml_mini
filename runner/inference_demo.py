"""Lightweight HTTP protocol fixture, not a trained inference model."""
import argparse,json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

class Handler(BaseHTTPRequestHandler):
    def send(self,value):
        data=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def do_GET(self):
        if self.path=='/healthz':self.send({'status':'ok','fixture':True})
        else:self.send_error(404)
    def do_POST(self):
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=65536:self.send_error(413);return
            observation=json.loads(self.rfile.read(size))['observation'];a=(observation['target']>observation['position'])-(observation['target']<observation['position']);self.send({'action':a})
        except (ValueError,KeyError,TypeError):self.send_error(400)
    def log_message(self,*args):pass

def main():
    p=argparse.ArgumentParser();p.add_argument('--host',required=True);p.add_argument('--port',required=True,type=int);a=p.parse_args()
    server=ThreadingHTTPServer((a.host,a.port),Handler)
    print('Protocol fixture listening on '+a.host+':'+str(a.port),flush=True);server.serve_forever()
if __name__=='__main__':main()
