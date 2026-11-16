"""DNS/address/TLS preflight faults, with transport boundary replaced."""
from contextlib import nullcontext
import socket
from types import SimpleNamespace

import pytest
from runner import cs_entry


@pytest.mark.parametrize('mode',['empty','blocked','connect','tls','fallback','timeout','failure'])
def test_preflight_addresses_and_deadline(monkeypatch,mode):
    addresses=[] if mode=='empty' else [(socket.AF_INET,socket.SOCK_STREAM,0,'',('127.0.0.1' if mode=='blocked' else '10.0.0.1',8000))]
    if mode=='fallback':addresses*=2
    monkeypatch.setattr(cs_entry.socket,'getaddrinfo',lambda *a,**kw:addresses)
    calls=[]
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def settimeout(self,value):assert value>=.1
        def connect(self,address):
            calls.append(address)
            if mode in ('timeout','failure') or (mode=='fallback' and len(calls)==1):raise OSError('connect failed')
    monkeypatch.setattr(cs_entry.socket,'socket',lambda *a:Socket())
    ticks=iter([0,2,3] if mode=='timeout' else [0]*10)
    monkeypatch.setattr(cs_entry.time,'monotonic',lambda:next(ticks))
    tls=[]
    def wrap(sock,server_hostname):tls.append(server_hostname);return nullcontext()
    monkeypatch.setattr(cs_entry.ssl,'create_default_context',lambda:SimpleNamespace(wrap_socket=wrap))
    endpoint=('https' if mode=='tls' else 'http')+'://inference.test:8000'
    if mode in ('empty','blocked','timeout','failure'):
        with pytest.raises((ValueError,RuntimeError)):cs_entry.preflight(endpoint,1)
    else:cs_entry.preflight(endpoint,1)
    if mode=='tls':assert tls==['inference.test']
    if mode=='fallback':assert len(calls)==2
    if mode=='blocked':assert calls==[]
