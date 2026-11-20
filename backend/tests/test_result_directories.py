"""Custom browsing freezes approved paths without changing execution results."""
import json
from copy import deepcopy
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from backend.app import result_directories as directories
from backend.app.core import ExecutionBrowseRoot, SharedArtifactRoot, Run, Worker, Slot, Job
from backend.tests.test_shared_artifacts import shared, route
from backend.tests.test_execution import env


def inputs(mode='host'):
    schema={'version':1,'fields':{'scene':{'type':'string'}}}
    template={'execution_mode':mode,'result_directory':'/approved/${business.scene}','parameter_schema':schema,
              'host_runtime':{'environment_name':'test','assets':['data']},'runtime':{'assets':{'data':'/assets'},'mounts':{'extra':{'source':'directories:extra','target':'/results'}}}}
    return dict(template=template,spec={'task_name':'demo','parameters':{'scene':'run1'}},
                storage={'workspace_root':'/work','cache_root':'/cache'},runtime={'assets':{'data':'/data'},'directories':{'extra':{'path':'/approved'}}},
                profile={'code_root':'/code'},execution='exec',output=None)


@pytest.mark.parametrize('path,expected',[
 ('/approved/${business.scene}','/approved/run1'),('${work_dir}/results','/work/runs/exec/work/results'),
 ('${output_dir}','/work/runs/exec/output'),('${cache_dir}/data','/cache/data'),('${assets:data}/${task_name}','/data/demo'),('/code/results','/code/results')])
def test_host_approved_directory_and_parameter_mapping(path,expected):
    args=inputs();args['template']['result_directory']=path
    assert directories.resolve(**args)==expected


@pytest.mark.parametrize('path,expected',[
 ('/results/${business.scene}','/approved/run1'),('${mounts:extra}/out','/approved/out'),('${assets:data}/x','/data/x'),
 ('${output_dir}/x','/work/runs/exec/output/x'),('${work_dir}/x','/work/runs/exec/work/x'),('${cache_dir}/x','/cache/x')])
def test_container_only_resolves_persistent_mounts(path,expected):
    args=inputs('docker');args['template']['result_directory']=path
    assert directories.resolve(**args)==expected


@pytest.mark.parametrize('path',['relative','/ok/../bad','${business.unknown}','${episode_id}','${assets:unknown}','${broken','/bad\npath'])
def test_invalid_declarations(path):
    with pytest.raises(ValueError):directories.validate(path,inputs()['template'])


@pytest.mark.parametrize('mode,path',[('host','/etc/private'),('docker','/temporary/data'),('host','${business.scene}')])
def test_unapproved_or_non_absolute_resolution(mode,path):
    args=inputs(mode);args['template']['result_directory']=path
    with pytest.raises(ValueError):directories.resolve(**args)


@pytest.mark.parametrize('value',[None,{},[],True,'../escape','/../../../etc'])
def test_runtime_parameter_cannot_escape_or_use_structured_values(value):
    args=inputs();args['spec']['parameters']['scene']=value
    with pytest.raises(ValueError):directories.resolve(**args)


def test_container_nested_mount_uses_longest_and_detects_ambiguity():
    args=inputs('docker');args['template']['result_directory']='/results/deep/file'
    args['template']['runtime']['mounts']['inner']={'source':'assets:data','target':'/results/deep'}
    assert directories.resolve(**args)=='/data/file'
    args['template']['runtime']['mounts']['collision']={'source':'output','target':'/results/deep'}
    with pytest.raises(ValueError,match='歧义'):directories.resolve(**args)


def test_custom_read_scope_media_and_metadata_are_independent(shared,monkeypatch):
    main,c,auth,_,job,task,output,mapping=shared
    other=output.parents[3]/'custom';other.mkdir();(other/'result.json').write_text('{"custom":true}')
    (other/'.mini-eval-artifacts').write_text('{"schema_version":1,"episode_id":3}')
    with main.SessionLocal() as s:
        s.add(ExecutionBrowseRoot(run_id=task['run_id'],execution_id=task['execution_id'],snapshot={'node_name':mapping['node_names'][0],'output_root':'/node/share/custom'}));s.commit()
    url=route(shared);r=c.get(url+'?directory=custom',headers=auth['alice']);assert r.status_code==200,r.text
    data=r.json();assert data['output_root']=='/node/share/custom';item=data['items'][0];assert item['episode_ids']==[]
    assert c.get(url+'?directory=custom',headers=auth['bob']).status_code==404
    grant=c.post(url+'/media-access',headers=auth['alice'],json={'directory':'custom','path':item['path'],'version':item['version']});assert grant.status_code==200,grant.text
    assert c.get(grant.json()['url']).json()=={'custom':True}
    assert c.get(url,headers=auth['alice']).status_code==200
    # Metadata does not need a shared mapping and never guesses current template paths.
    monkeypatch.setenv('MINI_EVAL_SHARED_STORAGE','[]')
    location=c.get(url.replace('/shared-artifacts','/execution-location'),headers=auth['alice']).json()
    assert location['result_directory']['path']=='/node/share/custom'
    assert c.post(url+'/media-access',headers=auth['alice'],json={'directory':'custom','path':item['path'],'version':item['version']}).status_code==409


def test_missing_custom_root_never_uses_current_template(shared):
    _,c,auth,*_=shared
    r=c.get(route(shared)+'?directory=custom',headers=auth['alice']);assert r.status_code==404
    assert '不会从当前模板' in r.json()['detail']


def test_first_claim_freezes_path_and_failed_resolution_is_diagnostic(shared):
    main,c,auth,_,job_id,task,output,mapping=shared
    with main.SessionLocal() as s:
        run=s.get(Run,task['run_id']);slot=s.get(Slot,run.slot_id);worker=s.get(Worker,slot.worker_id);job=s.get(Job,job_id)
        worker.storage_config={'workspace_root':'/node/share/work','assets_root':'/node/share/assets','cache_root':'/node/share/cache'}
        worker.runtime_config={'directories':{'extra':{'path':'/node/share/custom'}}}
        worker.host_environments={'test':{'python':'/usr/bin/python3','code_root':'/code'}}
        job.client_spec={**job.client_spec,'parameters':{'scene':'one'},'template':{**inputs()['template'],'result_directory':'/node/share/custom/${business.scene}'}}
        directories.plan(s,run,worker,slot,job,True);s.commit()
        assert directories.location(s,run)['output_root']=='/node/share/custom/one'
        worker.runtime_config={};job.client_spec={**job.client_spec,'parameters':{'scene':'two'}}
        directories.plan(s,run,worker,slot,job,True);s.commit()
        assert directories.location(s,run)['output_root']=='/node/share/custom/one'
        s.delete(s.get(ExecutionBrowseRoot,run.id));s.flush()
        directories.plan(s,run,worker,slot,job,False);assert s.get(ExecutionBrowseRoot,run.id) is None
        directories.plan(s,run,worker,slot,job,True);s.commit()
        with pytest.raises(HTTPException) as e:directories.location(s,run)
        assert e.value.status_code==409
        assert run.status==task['status']


def test_template_save_copy_and_host_edit_authorization(env):
    from backend.tests.test_template_versions import fields
    _,c,a=env
    body={'name':'browse','version':'1','execution_mode':'host','command':['python','business.py'],
          'host_runtime':{'environment_name':'approved'},'result_directory':'${output_dir}/results'}
    r=c.post('/api/v1/admin/client-templates',headers=a['admin'],json=body)
    assert r.status_code==201,r.text
    original=r.json();assert original['result_directory']==body['result_directory']
    r=c.post('/api/v1/runtime-templates/client/'+original['id']+'/clone',headers=a['alice'],json={'name':'copy','version':'1'})
    assert r.status_code==201,r.text
    draft=r.json();assert draft['result_directory']==body['result_directory']
    values=fields(draft);values['result_directory']='/etc'
    r=c.put('/api/v1/runtime-templates/client/'+draft['id'],headers=a['alice'],json={'expected_revision':0,'definition':values})
    assert r.status_code==403,r.text
    body['name']='bad';body['result_directory']='${business.missing}'
    assert c.post('/api/v1/admin/client-templates',headers=a['admin'],json=body).status_code==400


@pytest.mark.parametrize('path',[None,''])
def test_optional_directory_keeps_existing_templates(path):
    assert directories.validate(path,inputs()['template']) is None


@pytest.mark.parametrize('path',['/', '/a\\b','/a'+chr(127),'x'*4097])
def test_invalid_resolved_paths(path):
    with pytest.raises(ValueError):directories.absolute(path)


def test_execution_mismatch_rejected(shared):
    main,c,auth,_,job,task,output,mapping=shared
    with main.SessionLocal() as s:
        s.add(ExecutionBrowseRoot(run_id=task['run_id'],execution_id='another',snapshot={'output_root':'/node/share'}));s.commit()
    response=c.get(route(shared)+'?directory=custom',headers=auth['alice'])
    assert response.status_code==409
    assert '身份' in response.json()['detail']


def test_server_does_not_inherit_client_browse_setting():
    from backend.app.templates import ServerCreate
    from pydantic import ValidationError
    body={'name':'server','version':'1','image':'image:1','command':['python','server.py']}
    assert 'result_directory' not in ServerCreate(**body).model_dump()
    with pytest.raises(ValidationError):ServerCreate(**body,result_directory='/shared')


def test_unconfigured_mount_source_cannot_be_resolved():
    args=inputs('docker');args['runtime']['directories']={};args['template']['result_directory']='${mounts:extra}/file'
    with pytest.raises(ValueError,match='参数缺失'):directories.resolve(**args)


def test_missing_mapping_preserves_path_then_first_read_binds(shared,monkeypatch):
    main,c,auth,_,job_id,task,output,mapping=shared
    monkeypatch.setenv('MINI_EVAL_SHARED_STORAGE','[]')
    with main.SessionLocal() as s:
        run=s.get(Run,task['run_id']);slot=s.get(Slot,run.slot_id);worker=s.get(Worker,slot.worker_id);job=s.get(Job,job_id)
        worker.storage_config={'workspace_root':'/node/share/work','assets_root':'/node/share/assets','cache_root':'/node/share/cache'}
        job.client_spec={**job.client_spec,'template':{**inputs()['template'],'result_directory':'${output_dir}/reports'}}
        platform=s.get(SharedArtifactRoot,run.id)
        if platform:s.delete(platform);s.flush()
        s.add(SharedArtifactRoot(run_id=run.id,execution_id=run.execution_id,snapshot={'layout':'batch-v1','output_root':'/node/share/batch/job/exec'}));s.flush()
        directories.plan(s,run,worker,slot,job,True);s.commit()
        frozen=s.get(ExecutionBrowseRoot,run.id).snapshot
        assert frozen['output_root']=='/node/share/batch/job/exec/output/reports' and 'storage_id' not in frozen
        monkeypatch.setenv('MINI_EVAL_SHARED_STORAGE',json.dumps([mapping]))
        assert directories.location(s,run)['relative']=='batch/job/exec/output/reports'
