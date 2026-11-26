import asyncio
import httpx
from backend.tests import team_web_load as load


def test_shared_page_requests_keep_identity_and_record_gateway_failures(monkeypatch):
    real_client=httpx.AsyncClient
    real_sleep=asyncio.sleep
    seen=[];records=[]
    def handle(request):
        seen.append((request.headers['Authorization'],request.url.path))
        if request.url.path.endswith('/monitor'):return httpx.Response(503,text='<html>busy</html>')
        return httpx.Response(200,json={})
    monkeypatch.setattr(load.httpx,'AsyncClient',lambda **kwargs:real_client(**kwargs,transport=httpx.MockTransport(handle)))
    async def sleep(value):await real_sleep(.02 if value==5 else 0)
    monkeypatch.setattr(load.asyncio,'sleep',sleep)
    asyncio.run(load.browse('http://test',['test-a','test-b'],.01,lambda:'batch',lambda *r:records.append(r)))
    assert {token for token,path in seen}=={'Bearer test-a','Bearer test-b'}
    assert len(seen)==8 and len(records)==8
    assert sum(code=='200' for code,_,_,_ in records)==6
    assert sum(reason=='non_json_gateway_response' for _,_,reason,_ in records)==2
