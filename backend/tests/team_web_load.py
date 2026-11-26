"""Concurrent authenticated browsing for the disposable mixed-load driver."""
import asyncio
import time
import httpx


async def browse(base, tokens, seconds, batch_id, record):
    started=time.monotonic()
    async def user(index, token):
        async with httpx.AsyncClient(base_url=base,trust_env=False,timeout=15,
                headers={'Authorization':'Bearer '+token},
                limits=httpx.Limits(max_connections=4,max_keepalive_connections=4)) as client:
            # Model an online team with staggered 5-second refresh, plus three
            # requests from each opened detail page. No browser JS is executed.
            await asyncio.sleep((index % 100)*.05)
            while time.monotonic()-started<seconds:
                target=batch_id()
                paths=['batches?view=summary','pools']
                if target:paths.extend([f'batches/{target}',f'batches/{target}/monitor'])
                async def request(path):
                    began=time.monotonic()
                    try:
                        r=await client.get('/api/v1/'+path)
                        reason=None
                        if r.status_code==503:
                            try:reason=r.json().get('reason','unknown')
                            except (ValueError, AttributeError):reason='non_json_gateway_response'
                        record(str(r.status_code),time.monotonic()-began,reason,path)
                    except httpx.TransportError:
                        record('transport_error',time.monotonic()-began,None,path)
                await asyncio.gather(*(request(path) for path in paths))
                await asyncio.sleep(5)
    await asyncio.gather(*(user(index,token) for index,token in enumerate(tokens)))
