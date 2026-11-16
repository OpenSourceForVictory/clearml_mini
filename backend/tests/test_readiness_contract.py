from copy import deepcopy
import pytest
from runner.readiness_contract import validate, normalize, matches


def test_legacy_snapshot_and_independent_protocol():
    old={'health_type':'http','protocol':'wss','health_path':'/ready','startup_timeout':600}
    snapshot=deepcopy(old)
    c=normalize(old)
    assert old==snapshot
    assert (c['tls'],c['failure_threshold'],c['stale_after'])==(True,1,15)
    assert normalize({**old,'readiness':{'kind':'http','tls':False}})['tls'] is False
    c['headers']['test']='changed'
    assert normalize(old)['headers']=={}


@pytest.mark.parametrize('config',[
    {'kind':[]}, {'kind':{}}, {'kind':'websocket','mode':'message'}, {'path':'//evil'}, {'path':'/a\nb'},
    {'stale_after':5},{'timeout':float('nan')},{'success_threshold':True},
    {'port':True},{'headers':{'Authorization':'hidden'}},
    {'headers':{'Host':'elsewhere'}},{'headers':{'x':'a\rb'}},
    {'headers_from_env':{'Authorization':'bad name'}},
    {'headers':{'X':'one'},'headers_from_env':{'x':'TWO'}},
    {'expect':{'type':'json_equals','pointer':'/a~2','value':True}},
    {'kind':'tcp','expect':{'type':'text_equals','value':'ok'}},
    {'kind':'websocket','send_text':'hello'}, {'statuses':[302]},
    {'arbitrary_script':'raise'}, {'version':2},
])
def test_rejects_invalid_config(config):
    with pytest.raises(ValueError):validate(config)


def test_message_matchers():
    c=validate({'kind':'websocket','mode':'message','send_text':'{"type":"health"}',
                'expect':{'type':'json_equals','pointer':'/data/ready','value':True}})
    assert matches(c['expect'],'{"data":{"ready":true}}')
    assert not matches(c['expect'],'{"data":{"ready":1}}')
    assert not matches(c['expect'],'not json')
    assert matches({'type':'json_equals','pointer':'/a~1b/0','value':'ok'},'{"a/b":["ok"]}')
    assert matches({'type':'text_equals','value':'ready'},b'ready')
    assert not matches({'type':'text_equals','value':'ready'},b'ready\n')


def test_server_template_roundtrip():
    from backend.app.templates import ServerCreate
    body=dict(name='ws',version='v1',image='test:image',command=['python','server.py'],
              readiness={'kind':'websocket','path':'/ws'})
    t=ServerCreate(**body)
    saved=t.model_dump()
    assert saved['readiness']['kind']=='websocket'
    body['readiness']['path']='/changed'
    assert saved['readiness']['path']=='/ws'
    assert ServerCreate(**saved).model_dump()==saved
