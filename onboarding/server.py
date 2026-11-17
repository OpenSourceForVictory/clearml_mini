"""Local Unix socket bridge; only the API container receives the socket mount."""
import argparse
import json
import os
from pathlib import Path
import socket
import socketserver
import struct
import threading
from http.server import BaseHTTPRequestHandler
from .contracts import Rejected
from .executor import Operations


class Server(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
    daemon_threads=True

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        try:
            peer=struct.unpack('3i',self.connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]
            if peer not in (0,os.getuid()):raise Rejected('executor_peer_rejected')
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=65536:raise Rejected('executor_request_too_large')
            body=json.loads(self.rfile.read(length));actor=body['actor'];ops=self.server.operations
            if not isinstance(actor,str) or not actor:raise Rejected('actor_required')
            if self.path=='/health':value={'ready':True}
            elif self.path=='/list':value=ops.list(actor)
            elif self.path=='/check':value=ops.start(actor,body['form'],body['authorization'])
            elif self.path=='/confirm':value=ops.confirm(body['id'],actor)
            elif self.path=='/trust':value=ops.trust(body['id'],actor,body['fingerprint'])
            elif self.path=='/cancel':value=ops.cancel(body['id'],actor)
            else:raise Rejected('unknown_executor_operation')
            status=200
        except Rejected as exc:status=409;value={'error':str(exc)}
        except Exception:status=400;value={'error':'invalid_executor_request'}
        data=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--socket',type=Path,required=True);parser.add_argument('--state',type=Path,required=True);parser.add_argument('--api-url',required=True);a=parser.parse_args()
    import fcntl
    a.socket.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    guard=(a.socket.parent/'executor.lock').open('a');fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
    a.socket.unlink(missing_ok=True)
    ops=Operations(a.state,a.api_url);server=Server(str(a.socket),Handler);os.chmod(a.socket,0o600);server.operations=ops
    def expire():
        while True:
            threading.Event().wait(10);ops.expire()
    threading.Thread(target=expire,daemon=True).start()
    try:server.serve_forever()
    finally:server.server_close();a.socket.unlink(missing_ok=True);ops.close()

if __name__=='__main__':main()
