"""Generic HTTP + WS service example; no evaluation SDK is required.

python -m pip install aiohttp==3.14.5
MINI_EVAL_PORT=8000 python examples/services/readiness_server.py
"""
import asyncio
import json
import os
from aiohttp import web


async def main():
    state={'ready':False}
    async def health(request):return web.json_response(state,status=200 if state['ready'] else 503)
    async def ws(request):
        connection=web.WebSocketResponse()
        await connection.prepare(request)
        await connection.send_json(state)
        async for message in connection:
            if message.type==web.WSMsgType.TEXT:
                try:body=json.loads(message.data)
                except ValueError:body={}
                if body.get('type')=='health':await connection.send_json(state)
                else:await connection.send_json({'error':'example accepts health messages only'})
        return connection
    app=web.Application();app.router.add_get('/healthz',health);app.router.add_get('/ws',ws)
    runner=web.AppRunner(app);await runner.setup()
    ports={int(os.getenv('MINI_EVAL_PORT','8000')),int(os.getenv('MINI_EVAL_HEALTH_PORT',os.getenv('MINI_EVAL_PORT','8000')))}
    for port in ports:await web.TCPSite(runner,os.getenv('MINI_EVAL_BIND_HOST','127.0.0.1'),port).start()
    try:
        await asyncio.sleep(float(os.getenv('MODEL_LOAD_SECONDS','2')))
        state['ready']=True
        print('service ready',flush=True)
        await asyncio.Event().wait()
    finally:await runner.cleanup()


if __name__=='__main__':asyncio.run(main())
