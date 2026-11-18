"""Pre-launch connectivity guard. It never updates global service readiness."""
import asyncio
import ssl
import logging
import socket
import time
from urllib.parse import urlsplit


LOG = logging.getLogger('mini-eval-worker.connectivity')


async def _probe(endpoint,timeout):
    import aiohttp
    from async_timeout import timeout as deadline
    from runner.cs_contract import endpoint_parts
    parts,port=endpoint_parts(endpoint)
    async with deadline(timeout):
        class Resolver(aiohttp.AsyncResolver):
            async def resolve(self,*args,**kwargs):
                result=await super().resolve(*args,**kwargs)
                from runner.cs_contract import check_ip
                for entry in result:check_ip(entry['host'])
                return result
        resolver=Resolver()
        connector=aiohttp.TCPConnector(resolver=resolver,limit=1,force_close=True)
        async def redirect(*args):raise RuntimeError('client_service_redirect_rejected')
        trace=aiohttp.TraceConfig();trace.on_request_redirect.append(redirect)
        try:
            async with aiohttp.ClientSession(connector=connector,trust_env=False,trace_configs=[trace],timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                if parts.scheme in ('ws','wss') and parts.path not in ('','/'):
                    try:
                        async with session.ws_connect(endpoint,max_msg_size=65536,timeout=aiohttp.ClientWSTimeout(ws_close=.1)):
                            status=101
                    except aiohttp.WSServerHandshakeError as exc:
                        # An authentication challenge proves reachability. The business
                        # program owns its own credentials, not the Worker.
                        if exc.status not in (401,403):raise
                        status=exc.status
                    return "WEBSOCKET",status
                else:
                    # Any HTTP response proves transport reachability, without making
                    # assumptions about a business root path or credentials.
                    transport_url=parts._replace(scheme={'ws':'http','wss':'https'}.get(parts.scheme,parts.scheme)).geturl()
                    async with session.head(transport_url,allow_redirects=False) as response:
                        return "HEAD",response.status
        finally:
            await connector.close();await resolver.close()


def target(endpoint):
    """Only an origin is safe to log; paths and exception messages may hold secrets."""
    try:
        parts=urlsplit(endpoint)
        host=parts.hostname
        if parts.scheme not in ('http','https','ws','wss') or not host:
            return '<invalid>'
        if any(ord(c)<33 for c in host):return '<invalid>'
        if ':' in host:host='['+host+']'
        port=parts.port
        return parts.scheme+'://'+host+((':'+str(port)) if port else '')
    except (ValueError,TypeError):
        return '<invalid>'


def method_for(endpoint):
    try:
        parts=urlsplit(endpoint)
        return 'WEBSOCKET' if parts.scheme in ('ws','wss') and parts.path not in ('','/') else 'HEAD'
    except (ValueError,TypeError):
        return 'UNKNOWN'


def diagnosis(exc):
    name=type(exc).__name__
    cause=getattr(exc,'os_error',exc)
    if isinstance(exc,TimeoutError):reason='timeout'
    elif isinstance(exc,ssl.SSLError) or 'SSL' in name or 'Certificate' in name:reason='tls'
    elif isinstance(cause,socket.gaierror) or name=='ClientConnectorDNSError':reason='dns'
    elif isinstance(cause,ConnectionRefusedError):reason='connection_refused'
    elif name=='WSServerHandshakeError':reason='websocket_handshake'
    elif isinstance(exc,ValueError):reason='invalid_endpoint'
    elif isinstance(exc,ImportError):reason='dependency'
    elif isinstance(exc,OSError) or name.startswith(('ClientConnection','ServerDisconnected')):reason='connection_error'
    else:reason='unexpected_error'
    status=getattr(exc,'status',None)
    status=status if type(status) is int else '-'
    return f'reason={reason} exception={name} status={status}'


async def check(endpoint,timeout=3):
    started=time.monotonic()
    try:
        method,status=await _probe(endpoint,timeout)
    except Exception as exc:
        LOG.warning('client connectivity failed target=%s method=%s elapsed_ms=%.1f timeout_s=%s %s',
                    target(endpoint),method_for(endpoint),(time.monotonic()-started)*1000,timeout,diagnosis(exc))
        raise
    LOG.info('client connectivity passed target=%s method=%s status=%s elapsed_ms=%.1f timeout_s=%s',
             target(endpoint),method,status,(time.monotonic()-started)*1000,timeout)


def verify(endpoint,timeout=3):
    try:asyncio.run(check(endpoint,timeout))
    except Exception as exc:
        raise RuntimeError(f'client_service_unreachable: target={target(endpoint)} timeout_s={timeout} '
                           f'{diagnosis(exc)}; inspect client Worker connectivity logs, then retry') from None
