"""Pre-launch connectivity guard. It never updates global service readiness."""
import asyncio
import ssl
import logging
import socket
import time
from urllib.parse import urlsplit


LOG = logging.getLogger('mini-eval-worker.connectivity')


async def _transport(parts,port):
    from runner.cs_contract import check_ip
    addresses=await asyncio.get_running_loop().getaddrinfo(parts.hostname,port,type=socket.SOCK_STREAM)
    if not addresses:raise socket.gaierror('no addresses')
    # Pin validated numeric addresses; never resolve the host again when connecting.
    for family,_,_,_,address in addresses:check_ip(address[0])
    context=ssl.create_default_context() if parts.scheme=='wss' else None
    last_error=None
    for family,_,_,_,address in addresses:
        try:
            _,writer=await asyncio.open_connection(address[0],port,family=family,
                ssl=context,server_hostname=parts.hostname if context else None)
        except OSError as exc:
            last_error=exc
            continue
        try:
            writer.close()
            await writer.wait_closed()
        finally:
            writer.close()
        return ('TLS' if context else 'TCP'),'-'
    raise last_error


async def _probe(endpoint,timeout):
    import aiohttp
    from async_timeout import timeout as deadline
    from runner.cs_contract import endpoint_parts
    parts,port=endpoint_parts(endpoint)
    async with deadline(timeout):
        if parts.scheme in ('ws','wss'):
            return await _transport(parts,port)
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
                # HTTP responses, including 405, prove transport reachability.
                async with session.head(endpoint,allow_redirects=False) as response:
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
        return {'ws':'TCP','wss':'TLS'}.get(parts.scheme,'HEAD')
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
    elif name=='ClientResponseError':reason='http_response'
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
