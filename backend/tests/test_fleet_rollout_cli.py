"""Release transaction orchestration with durable journals and isolated external effects."""
import json
import sys
from pathlib import Path

import pytest
from scripts import fleet_rollout as rollout


@pytest.fixture
def release(tmp_path,monkeypatch):
    plan={'targets':[{'address':address,'node_name':name} for address,name in rollout.TARGETS.items()],
          'target_worker':'test','target_protocol':14,'target_agent':'test','target_database':'head'}
    plan_file=tmp_path/'plan.json';plan_file.write_text(json.dumps(plan))
    token=tmp_path/'token.json';token.write_text('{"token":"fixture-token"}')
    state=tmp_path/'release';events=[]
    active={'Config':{'Labels':{'com.docker.compose.project':'mini-eval'},'Image':'old:1'},'Image':'sha256:old','HostConfig':{'PortBindings':{}}}
    def run(argv):
        events.append(('command',argv))
        if argv[:3]==['docker','image','inspect']:return '[{"Id":"sha256:new"}]'
        if argv[:2]==['docker','inspect']:return json.dumps([active])
        return ''
    monkeypatch.setattr(rollout,'run',run)
    monkeypatch.setattr(rollout,'probe',lambda address:{'address':address})
    monkeypatch.setattr(rollout,'verify_identity',lambda target,observed:events.append(('identity',target['node_name'])))
    monkeypatch.setattr(rollout,'check_image',lambda image,plan:events.append(('image',image)))
    monkeypatch.setattr(rollout,'check_compose_environment',lambda command,active:None)
    monkeypatch.setattr(rollout,'compose_command',lambda *a:['compose-test'])
    def api(path,body,method):
        assert path=='admin/worker-releases' and method=='POST';events.append(('publish',None));return {'id':'release'}
    monkeypatch.setattr(rollout,'API',lambda *args:api)
    def drain(api,plan,journal,timeout):
        events.append(('drain',None))
        for target in plan['targets']:
            key='drain:'+target['node_name'];journal.begin(key,{})
            journal.state['steps'][key]['was_enabled']=True;journal.finish(key)
    def upgrade(api,plan,journal,identity):
        events.append(('upgrade',identity))
        for target in plan['targets']:
            key='upgrade:'+target['node_name'];journal.begin(key,{});journal.finish(key)
    def restore(api,plan,journal):
        events.append(('restore',None))
        for target in plan['targets']:
            key='drain:'+target['node_name'];journal.state['steps'][key]['restored']=True;journal.finish(key)
    monkeypatch.setattr(rollout,'drain',drain);monkeypatch.setattr(rollout,'upgrade_nodes',upgrade)
    monkeypatch.setattr(rollout,'restore_admission',restore)
    monkeypatch.setattr(rollout,'assert_release_ready',lambda *a:events.append(('ready',None)))
    monkeypatch.setattr(rollout,'backup_database',lambda path:events.append(('backup',str(path))) or 'digest')
    monkeypatch.setattr(rollout,'restore_probe',lambda *args:events.append(('restore-probe',None)) or {'passed':True})
    def invoke(action='apply',*extra):
        monkeypatch.setattr(sys,'argv',['rollout',action,'--plan',str(plan_file),'--token-file',str(token),
            '--state-directory',str(state),'--release-id','test','--image','release:1',*extra])
        rollout.main()
    return invoke,events,state,plan_file,active


def test_release_order_and_repeat_does_not_redeploy(release):
    invoke,events,state,plan_file,active=release
    invoke()
    names=[e[0] for e in events]
    assert names.index('drain')<names.index('backup')<names.index('restore-probe')<names.index('publish')<names.index('upgrade')<names.index('ready')<names.index('restore')
    commands=[value for kind,value in events if kind=='command' and value[0]=='compose-test']
    assert commands==[['compose-test','stop','api'],['compose-test','run','--rm','--no-deps','migrate'],['compose-test','up','-d','--no-deps','--wait','api']]
    assert json.loads((state/'previous-deployment.json').read_text())['image_id']=='sha256:old'
    events.clear();invoke('resume')
    assert not any(name in ('drain','backup','publish','upgrade','restore') for name,value in events)


def test_check_is_read_only(release):
    invoke,events,state,plan_file,active=release;invoke('check')
    assert not (state/'test.json').exists()
    assert not any(name in ('drain','backup','publish') for name,value in events)


@pytest.mark.parametrize('mode',['bad-plan','wrong-container','legacy-journal','missing-release-reconcile','invalid-image'])
def test_release_refuses_unsafe_inputs_before_draining(release,mode):
    invoke,events,state,plan_file,active=release;extra=[]
    if mode=='bad-plan':plan_file.write_text('{"targets":[]}')
    if mode=='wrong-container':active['Config']['Labels']['com.docker.compose.project']='other'
    if mode=='legacy-journal':
        state.mkdir();(state/'test.json').write_text('{"release_id":"test","steps":{"backup":{"status":"done"}}}')
    if mode=='missing-release-reconcile':extra=['--reconcile-node','node']
    if mode=='invalid-image':extra=['--image','bad;command']
    with pytest.raises((ValueError,RuntimeError,SystemExit)):invoke('apply',*extra)
    assert not any(name=='drain' for name,value in events)


def test_admission_restore_requires_published_release_check(release):
    invoke,events,state,plan_file,active=release;invoke();events.clear()
    invoke('restore-admission')
    names=[e[0] for e in events]
    assert names[-2:]==['ready','restore']
    assert not any(name in ('backup','publish','upgrade') for name in names)
