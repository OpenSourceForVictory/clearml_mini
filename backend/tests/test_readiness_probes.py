import asyncio
import time
import pytest
from aiohttp import web
from runner.readiness_contract import validate
from worker.readiness import probe


async def with_server(check):
    async def handler(request):
        mode=request.match_info['mode']
        if mode=='redirect':raise web.HTTPFound('/http')
        if mode=='auth' and request.headers.get('Authorization')!='Bearer test':raise web.HTTPUnauthorized()
        if mode=='http':return web.json_response({'ready':True})
        if mode=='large':return web.Response(text='x'*70000)
        if mode=='slow':await asyncio.sleep(1);return web.Response(text='ready')
        ws=web.WebSocketResponse(protocols=['health']);await ws.prepare(request)
        if mode=='receive':await ws.send_json({'ready':True})
        if mode=='many':
            for _ in range(17):await ws.send_str('wrong')
        async for msg in ws:
            if mode=='echo':await ws.send_str(msg.data)
            if mode=='auth':await ws.send_json({'ready':True})
        return ws
    app=web.Application();app.router.add_get('/{mode}',handler)
    runner=web.AppRunner(app);await runner.setup()
    site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    port=site._server.sockets[0].getsockname()[1]
    try:await check(port)
    finally:await runner.cleanup()


def test_protocols(monkeypatch):
    monkeypatch.setenv('HTTP_PROXY','http://127.0.0.1:1')
    monkeypatch.setenv('HEALTH_TOKEN','Bearer test')
    async def check(port):
        async def run(**kwargs):return await probe('127.0.0.1',port,validate(kwargs),{'probe_environment':['HEALTH_TOKEN']})
        assert (await run(kind='tcp'))['ready']
        assert (await run(kind='http',path='/http',expect={'type':'json_equals','pointer':'/ready','value':True}))['ready']
        assert not (await run(kind='http',path='/http',expect={'type':'json_equals','pointer':'/ready','value':False}))['ready']
        assert (await run(kind='http',path='/redirect'))['error_code']=='redirect_rejected'
        assert (await run(kind='http',path='/large',expect={'type':'text_equals','value':'ready'}))['error_code']=='response_too_large'
        assert (await run(kind='websocket',path='/echo',subprotocols=['health']))['ready']
        assert (await run(kind='websocket',path='/receive',mode='message',expect={'type':'json_equals','pointer':'/ready','value':True}))['ready']
        assert (await run(kind='websocket',path='/echo',mode='message',send_text='ready',expect={'type':'text_equals','value':'ready'}))['ready']
        assert (await run(kind='websocket',path='/many',mode='message',expect={'type':'text_equals','value':'ready'}))['error_code']=='response_mismatch'
        assert (await run(kind='websocket',path='/redirect'))['error_code']=='redirect_rejected'
        assert (await run(kind='websocket',path='/auth'))['error_code']=='authentication_failed'
        assert (await run(kind='websocket',path='/auth',headers_from_env={'Authorization':'HEALTH_TOKEN'}))['ready']
        assert (await run(kind='websocket',path='/auth',headers_from_env={'Authorization':'NOT_APPROVED'}))['error_code']=='configuration_invalid'
    asyncio.run(with_server(check))


def test_bounded_timeout_and_cancellation():
    async def check(port):
        before=time.monotonic()
        c=validate({'kind':'websocket','path':'/echo','mode':'message','timeout':.1,'expect':{'type':'text_equals','value':'ready'}})
        result=await probe('127.0.0.1',port,c)
        assert result['error_code']=='timeout'
        assert time.monotonic()-before<.8
        task=asyncio.create_task(probe('127.0.0.1',port,{**c,'timeout':30}))
        await asyncio.sleep(.05);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(with_server(check))


def test_credentials_never_include_unapproved_values(monkeypatch,tmp_path):
    from worker.probe_credentials import available,resolve
    monkeypatch.setenv('APP_SECRET','do-not-leak')
    assert available({'probe_environment':['MISSING']})==[]
    config=validate({'headers_from_env':{'Authorization':'APP_SECRET'}})
    with pytest.raises(ValueError,match='not approved'):resolve(config,{})
    assert resolve(config,{'probe_environment':['APP_SECRET']})[0]['Authorization']=='do-not-leak'
    monkeypatch.setenv('APP_SECRET','bad\r\nheader')
    with pytest.raises(ValueError,match='invalid probe credential'):resolve(config,{'probe_environment':['APP_SECRET']})


def test_tls_ca_and_invalid_certificate(tmp_path):
    import ssl,subprocess
    key=tmp_path/'key.pem';cert=tmp_path/'cert.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1'],check=True,capture_output=True)
    async def run():
        async def handler(request):
            if request.path=='/ws':
                ws=web.WebSocketResponse();await ws.prepare(request)
                async for _ in ws:pass
                return ws
            return web.Response(text='ready')
        app=web.Application();app.router.add_get('/{mode}',handler)
        runner=web.AppRunner(app);await runner.setup()
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(cert,key)
        site=web.TCPSite(runner,'127.0.0.1',0,ssl_context=ctx);await site.start()
        port=site._server.sockets[0].getsockname()[1]
        import os
        os.environ['TEST_PROBE_CA']=str(cert)
        try:
            for kind,path in [('http','/http'),('websocket','/ws')]:
                c=validate({'kind':kind,'tls':True,'path':path})
                assert (await probe('127.0.0.1',port,c))['error_code']=='tls_failed'
                c['ca_env']='TEST_PROBE_CA'
                assert (await probe('127.0.0.1',port,c,{'probe_environment':['TEST_PROBE_CA']}))['ready']
        finally:os.environ.pop('TEST_PROBE_CA');await runner.cleanup()
    asyncio.run(run())


def test_client_guard_transport_independent_of_business_protocol(monkeypatch):
    monkeypatch.setattr("runner.cs_contract.check_ip",lambda host:None)
    from worker.client_connectivity import check
    async def run(port):
        await check(f'http://127.0.0.1:{port}/http')
        await check(f'ws://127.0.0.1:{port}/echo')
        await check(f'ws://127.0.0.1:{port}/auth')
        # Transport reachability is independent of business handshake/path.
        await check(f'ws://127.0.0.1:{port}/http')
    asyncio.run(with_server(run))


def test_node_probe_concurrency_lock_and_cancellation(tmp_path):
    import fcntl,threading
    from worker.readiness import ProbeRunner
    locks=[]
    for n in range(4):
        f=(tmp_path/f'probe-{n}.lock').open('a');fcntl.flock(f,fcntl.LOCK_EX);locks.append(f)
    c=validate({'kind':'tcp','interval':.5,'timeout':.1})
    runner=ProbeRunner({'address':'127.0.0.1','port':1,'readiness':c},{'api_url':'http://127.0.0.1:1','token':'test'},None,'test','epoch',tmp_path)
    try:
        time.sleep(.15);assert runner.last is None
        locks.pop().close()
        end=time.monotonic()+2
        while runner.last is None and time.monotonic()<end:time.sleep(.01)
        assert runner.last is not None
        begin=time.monotonic();runner.close();assert time.monotonic()-begin<1
        assert not runner.thread.is_alive()
    finally:
        runner.close()
        for f in locks:f.close()


def test_dependencies_do_not_import_old_client_before_upgrade(monkeypatch):
    import importlib.metadata
    import sys
    from worker.probe_dependencies import ready,PACKAGES
    monkeypatch.setattr(importlib.metadata,'version',lambda name:dict(x.split('==') for x in PACKAGES)[name])
    assert ready()
    monkeypatch.setattr(importlib.metadata,'version',lambda name:'0.0.1')
    assert not ready()


def test_pip_bootstrap_rejects_corrupt_download_and_preserves_cache(monkeypatch,tmp_path):
    import importlib.util,io,urllib.request
    from worker.probe_dependencies import pip_command
    monkeypatch.setattr(importlib.util,'find_spec',lambda name:None)
    cache=tmp_path/'.mini-eval-pip-25.2.pyz';cache.write_bytes(b'corrupted')
    monkeypatch.setattr(urllib.request,'urlopen',lambda *a,**k:io.BytesIO(b'bad download'))
    with pytest.raises(RuntimeError,match='checksum mismatch'):pip_command(tmp_path)
    assert cache.read_bytes()==b'corrupted'
    assert list(tmp_path.iterdir())==[cache]


def test_client_guard_base_ws_url_timeout_and_cancel(monkeypatch):
    monkeypatch.setattr('runner.cs_contract.check_ip',lambda host:None)
    from worker.client_connectivity import check
    async def run(port):
        # Base WS URL can legitimately append a business-specific path later.
        await check(f'ws://127.0.0.1:{port}')
        before=time.monotonic()
        with pytest.raises(TimeoutError):await check(f'http://127.0.0.1:{port}/slow',timeout=.1)
        assert time.monotonic()-before<.8
        task=asyncio.create_task(check(f'http://127.0.0.1:{port}/slow'))
        await asyncio.sleep(.05);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        # One client's failed connection has no effect on the reachable service.
        await check(f'http://127.0.0.1:{port}/http')
    asyncio.run(with_server(run))
