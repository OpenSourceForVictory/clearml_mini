import asyncio
import logging
import socket
import ssl
import subprocess

import pytest
from worker.client_connectivity import check


@pytest.mark.parametrize('path', ['', '/', '/requires-auth'])
def test_ws_sends_no_http_or_handshake(monkeypatch, caplog, path):
    monkeypatch.setattr('runner.cs_contract.check_ip', lambda host: None)
    monkeypatch.setenv('ALL_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    caplog.set_level(logging.INFO)
    async def run():
        received = asyncio.get_running_loop().create_future()
        async def serve(reader, writer):
            data = await reader.read(4096)
            if data:
                writer.write(b'HTTP/1.1 400 Bad Request\r\nContent-Length: 29\r\nConnection: close\r\n\r\nunsupported http method: HEAD\n')
                await writer.drain()
            writer.close(); await writer.wait_closed()
            received.set_result(data)
        server = await asyncio.start_server(serve, '127.0.0.1', 0)
        try:
            await check(f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}{path}')
            assert await asyncio.wait_for(received, 1) == b''
        finally:
            server.close(); await server.wait_closed()
    asyncio.run(run())
    assert 'method=TCP status=-' in caplog.text


def test_wss_validates_certificate_and_sends_no_application_data(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr('runner.cs_contract.check_ip', lambda host: None)
    caplog.set_level(logging.INFO)
    key=tmp_path/'key.pem';cert=tmp_path/'cert.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1'],check=True,capture_output=True)
    async def run():
        received=asyncio.get_running_loop().create_future()
        async def serve(reader,writer):
            data=await reader.read(100)
            writer.close();await writer.wait_closed()
            received.set_result(data)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
        server=await asyncio.start_server(serve,'127.0.0.1',0,ssl=context)
        endpoint=f'wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/private'
        try:
            with pytest.raises(ssl.SSLCertVerificationError):await check(endpoint)
            monkeypatch.setenv('SSL_CERT_FILE',str(cert))
            await check(endpoint)
            assert await asyncio.wait_for(received,1)==b''
        finally:
            server.close();await server.wait_closed()
    asyncio.run(run())
    assert 'method=TLS status=-' in caplog.text
    assert 'reason=tls' in caplog.text


def test_dns_checked_before_connect(monkeypatch):
    async def run():
        async def resolve(*args,**kwargs):
            return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',19000))]
        async def forbidden(*args,**kwargs):
            pytest.fail('must reject unsafe DNS before connecting')
        monkeypatch.setattr(asyncio.get_running_loop(),'getaddrinfo',resolve)
        monkeypatch.setattr(asyncio,'open_connection',forbidden)
        with pytest.raises(ValueError,match='reachable external'):await check('ws://service.example:19000/')
    asyncio.run(run())


def test_multiple_addresses_fallback_and_no_second_dns(monkeypatch):
    monkeypatch.setattr('runner.cs_contract.check_ip', lambda host: None)
    async def run():
        resolved=[]
        async def serve(reader,writer):
            await reader.read();writer.close();await writer.wait_closed()
        server=await asyncio.start_server(serve,'127.0.0.1',0)
        port=server.sockets[0].getsockname()[1]
        async def resolve(host,port,**kwargs):
            resolved.append(host)
            return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,port)) for ip in ['127.0.0.2','127.0.0.1']]
        monkeypatch.setattr(asyncio.get_running_loop(),'getaddrinfo',resolve)
        try:
            await check(f'ws://service.example:{port}/')
            assert resolved==['service.example']
        finally:server.close();await server.wait_closed()
    asyncio.run(run())


@pytest.mark.parametrize('mode',['refused','empty_dns','timeout','cancel'])
def test_transport_failures(monkeypatch, mode):
    monkeypatch.setattr('runner.cs_contract.check_ip', lambda host: None)
    async def run():
        async def resolve(*args,**kwargs):
            return [] if mode=='empty_dns' else [(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',19000))]
        async def connect(*args,**kwargs):
            if mode=='refused':raise ConnectionRefusedError(111,'refused')
            if mode=='cancel':raise asyncio.CancelledError()
            await asyncio.sleep(10)
        monkeypatch.setattr(asyncio.get_running_loop(),'getaddrinfo',resolve)
        monkeypatch.setattr(asyncio,'open_connection',connect)
        error={'refused':ConnectionRefusedError,'empty_dns':socket.gaierror,'timeout':TimeoutError,'cancel':asyncio.CancelledError}[mode]
        with pytest.raises(error):await check('ws://service.example:19000/',timeout=.02)
    asyncio.run(run())
