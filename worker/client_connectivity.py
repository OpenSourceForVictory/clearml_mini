"""Pre-launch connectivity guard. It never updates global service readiness."""
import asyncio
import ssl
from urllib.parse import urlsplit


async def check(endpoint,timeout=3):
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
                if parts.scheme in ('ws','wss'):
                    try:
                        async with session.ws_connect(endpoint,max_msg_size=65536,timeout=aiohttp.ClientWSTimeout(ws_close=.1)):pass
                    except aiohttp.WSServerHandshakeError as exc:
                        # An authentication challenge proves reachability. The business
                        # program owns its own credentials, not the Worker.
                        if exc.status not in (401,403):raise
                else:
                    # Any HTTP response proves transport reachability, without making
                    # assumptions about a business root path or credentials.
                    async with session.head(endpoint,allow_redirects=False):pass
        finally:
            await connector.close();await resolver.close()


def verify(endpoint,timeout=3):
    try:asyncio.run(check(endpoint,timeout))
    except Exception:
        raise RuntimeError('client_service_unreachable: assigned endpoint cannot be reached from this client node; inspect network and service address, then retry') from None
