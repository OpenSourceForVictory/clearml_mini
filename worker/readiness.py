"""Bounded async protocol probes, independent of execution/heartbeat loops."""
import asyncio
import contextlib
import ssl
import time
from runner.readiness_contract import matches
from .probe_credentials import resolve

MAX_BYTES=65536
MAX_MESSAGES=16


class ProbeFailure(Exception):
    def __init__(self,code):self.code=code


async def probe(address,port,config,local=None):
    import aiohttp
    started=time.monotonic();phase='configuration'
    try:
        headers,ca=resolve(config,local or {})
        context=ssl.create_default_context(cafile=ca) if config['tls'] else False
        from async_timeout import timeout
        async with timeout(config['timeout']):
            phase='connect'
            if config['kind']=='tcp':
                _,writer=await asyncio.open_connection(address,port)
                writer.close()
                await writer.wait_closed()
            else:
                # Async DNS avoids unbounded background getaddrinfo threads.
                resolver=aiohttp.AsyncResolver()
                connector=aiohttp.TCPConnector(resolver=resolver,limit=1,force_close=True)
                async def no_redirect(session,ctx,params):raise ProbeFailure('redirect_rejected')
                trace=aiohttp.TraceConfig();trace.on_request_redirect.append(no_redirect)
                try:
                    async with aiohttp.ClientSession(connector=connector,trust_env=False,auto_decompress=False,
                            timeout=aiohttp.ClientTimeout(total=config['timeout']),trace_configs=[trace],
                            cookie_jar=aiohttp.DummyCookieJar()) as session:
                        scheme=('wss' if config['tls'] else 'ws') if config['kind']=='websocket' else ('https' if config['tls'] else 'http')
                        host='['+address+']' if ':' in address else address
                        url=f'{scheme}://{host}:{port}{config["path"]}'
                        phase='handshake' if config['kind']=='websocket' else 'response'
                        if config['kind']=='http':
                            async with session.get(url,headers=headers,ssl=context,allow_redirects=False) as response:
                                if 300<=response.status<400:raise ProbeFailure('redirect_rejected')
                                if response.status in (401,403):raise ProbeFailure('authentication_failed')
                                if response.status not in config['statuses']:raise ProbeFailure('unexpected_status')
                                if config['expect'] is not None:
                                    data=bytearray()
                                    async for chunk in response.content.iter_chunked(8192):
                                        data.extend(chunk)
                                        if len(data)>MAX_BYTES:raise ProbeFailure('response_too_large')
                                    if not matches(config['expect'],bytes(data)):raise ProbeFailure('response_mismatch')
                        else:
                            async with session.ws_connect(url,headers=headers,ssl=context,protocols=config['subprotocols'],
                                    max_msg_size=MAX_BYTES,compress=0,autoping=False,
                                    timeout=aiohttp.ClientWSTimeout(ws_receive=config['timeout'],ws_close=.1)) as ws:
                                if config['subprotocols'] and ws.protocol not in config['subprotocols']:raise ProbeFailure('subprotocol_mismatch')
                                if config['mode']=='message':
                                    phase='message'
                                    if config['send_text'] is not None:await ws.send_str(config['send_text'])
                                    for _ in range(MAX_MESSAGES):
                                        msg=await ws.receive()
                                        if msg.type==aiohttp.WSMsgType.TEXT and matches(config['expect'],msg.data):break
                                        if msg.type==aiohttp.WSMsgType.PING:await ws.pong(msg.data)
                                        if msg.type in (aiohttp.WSMsgType.CLOSE,aiohttp.WSMsgType.CLOSED):raise ProbeFailure('connection_closed')
                                        if msg.type==aiohttp.WSMsgType.ERROR:raise ProbeFailure('invalid_message')
                                    else:raise ProbeFailure('response_mismatch')
                finally:
                    await connector.close()
                    await resolver.close()
        error=None
    except asyncio.CancelledError:raise
    except ProbeFailure as exc:error=exc.code
    except (ssl.SSLError,aiohttp.ClientConnectorCertificateError,aiohttp.ClientConnectorSSLError):error='tls_failed'
    except aiohttp.WSServerHandshakeError as exc:error='authentication_failed' if exc.status in (401,403) else 'handshake_failed'
    except (TimeoutError,asyncio.TimeoutError):error='timeout'
    except ValueError:error='configuration_invalid'
    except (OSError,aiohttp.ClientError):error='connection_failed'
    return dict(ready=error is None,error=error,error_code=error,phase=phase,duration_ms=round((time.monotonic()-started)*1000))


class ProbeRunner:
    """One probe per execution; a bounded process lock gates node-wide concurrency."""
    def __init__(self,service,local,api,execution,epoch,lock_root):
        import threading
        self.service=service;self.local=local;self.api=api;self.execution=execution;self.epoch=epoch
        self.lock_root=lock_root;self.last=None;self.accepted_ready=False;self.error=None
        self.loop=asyncio.new_event_loop();self.started=threading.Event()
        self.thread=threading.Thread(target=self._run,daemon=True)
        self.thread.start();self.started.wait(2)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.task=self.loop.create_task(self._poll());self.started.set()
        try:self.loop.run_until_complete(self.task)
        except asyncio.CancelledError:pass
        except Exception:self.error='service probe execution failed'
        finally:self.loop.close()

    async def _poll(self):
        import aiohttp
        import fcntl
        import random
        from pathlib import Path
        c=self.service['readiness'];sequence=0
        root=Path(self.lock_root);root.mkdir(parents=True,exist_ok=True,mode=0o700)
        async with aiohttp.ClientSession(trust_env=False,timeout=aiohttp.ClientTimeout(total=3)) as session:
            while True:
                held=None
                for n in range(4):
                    f=(root/f'probe-{n}.lock').open('a')
                    try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);held=f;break
                    except BlockingIOError:f.close()
                if held is None:
                    await asyncio.sleep(.1);continue
                try:result=await probe(self.service['address'],self.service.get('health_port',self.service['port']),c,self.local)
                finally:held.close()
                sequence+=1;sampled=time.monotonic();self.last=result
                body={**result,'epoch':self.epoch,'sequence':sequence,'sample_age_ms':0}
                try:
                    url=self.local['api_url'].rstrip('/')+f'/api/v1/worker/executions/{self.execution}/service-health'
                    body['sample_age_ms']=int((time.monotonic()-sampled)*1000)
                    async with session.post(url,json=body,headers={'Authorization':'Bearer '+self.local['token']},allow_redirects=False) as response:
                        if response.status==200:
                            data=await response.json();self.accepted_ready=bool(data.get('ready'))
                        elif response.status in (400,401,403,404,409,422):
                            self.error='service probe report rejected';return
                except (aiohttp.ClientError,TimeoutError):pass
                await asyncio.sleep(c['interval']+random.uniform(0,c['interval']*.05))

    def close(self):
        if self.thread.is_alive():
            self.loop.call_soon_threadsafe(self.task.cancel);self.thread.join(5)
