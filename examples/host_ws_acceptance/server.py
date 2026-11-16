"""Mock business WS endpoint and independent WS readiness endpoint."""
import json,os,time,asyncio
from pathlib import Path
from aiohttp import web
c=json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
started=time.monotonic()
async def handle(request):
    ws=web.WebSocketResponse();await ws.prepare(request)
    if request.path=='/health':
        await ws.send_json({'ready':time.monotonic()-started>=5})
    async for message in ws:
        if message.type==web.WSMsgType.TEXT:
            value=json.loads(message.data)
            await ws.send_json({'echo':value,'model':c['business']['model'],'server_execution':c['platform']['execution_id']})
    return ws
async def main():
    app=web.Application();app.router.add_get('/health',handle);app.router.add_get('/infer',handle)
    runner=web.AppRunner(app);await runner.setup()
    for port in {int(os.environ['MINI_EVAL_PORT']),int(os.environ['MINI_EVAL_HEALTH_PORT'])}:
        await web.TCPSite(runner,os.environ['MINI_EVAL_BIND_HOST'],port).start()
    print(json.dumps({'host_ws_server':True,'pid':os.getpid()}),flush=True)
    await asyncio.Event().wait()
asyncio.run(main())
