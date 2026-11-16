"""Small real ACL allocation and HTTP echo service for platform connection checks."""
import argparse
import ctypes
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',default='fleet-test-model')
    parser.add_argument('--allocation-mb',type=int,default=8)
    args=parser.parse_args()
    if not 1<=args.allocation_mb<=64:parser.error('allocation must be 1..64 MiB')
    config=json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
    acl=ctypes.CDLL('libascendcl.so')
    assert acl.aclInit(None)==0
    count=ctypes.c_uint()
    assert acl.aclrtGetDeviceCount(ctypes.byref(count))==0 and count.value==1
    assert acl.aclrtSetDevice(0)==0
    pointer=ctypes.c_void_p()
    assert acl.aclrtMalloc(ctypes.byref(pointer),ctypes.c_size_t(args.allocation_mb*1024**2),0)==0
    details={'model':args.model,'server_execution':config['platform']['execution_id'],
             'device_count':count.value,'allocated_mb':args.allocation_mb,'uid':os.getuid()}

    class Handler(BaseHTTPRequestHandler):
        def send_json(self,value,status=200):
            data=json.dumps(value).encode();self.send_response(status)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)))
            self.end_headers();self.wfile.write(data)
        def do_GET(self):self.send_json(details)
        def do_POST(self):
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=65536:self.send_json({'error':'invalid payload size'},413);return
            self.send_json({**details,'received':json.loads(self.rfile.read(size))})

    print(json.dumps(details),flush=True)
    try:
        listen=config['platform']['listen']
        ThreadingHTTPServer((listen['address'],listen['port']),Handler).serve_forever()
    finally:
        acl.aclrtFree(pointer);acl.aclrtResetDevice(0);acl.aclFinalize()


if __name__=='__main__':main()
