import json
from pathlib import Path

import pytest

from runner.artifact_manifest import NAME, register
from worker.artifact_reader import respond


def listing(root, path=''):
    st=root.stat()
    return respond(str(root),[st.st_dev,st.st_ino],{'operation':'list','path':path})


def test_explicit_directory_metadata_not_names_and_nested_files(tmp_path):
    root=tmp_path/'output';root.mkdir()
    folder=root/'arbitrary';folder.mkdir();(folder/'video.mp4').write_bytes(b'video')
    nested=folder/'nested';nested.mkdir();(nested/'result.json').write_text('{}')
    fake=root/'episode-7';fake.mkdir();(fake/'x').write_text('x')
    assert register(folder,3)
    assert register(folder,3)
    assert not register(folder,4)
    assert listing(root,'arbitrary')['items'][0]['episode_ids']==[3]
    assert listing(root,'arbitrary/nested')['items'][0]['episode_ids']==[3]
    assert listing(root,'episode-7')['items'][0]['episode_ids']==[]
    assert NAME not in [x['name'] for x in listing(root,'arbitrary')['items']]
    assert listing(root,NAME)['status']=='invalid_path'


@pytest.mark.parametrize('value',[{},[],{'schema_version':True,'episode_id':1},
    {'schema_version':1,'episode_id':True},{'schema_version':1,'episode_id':0},
    {'schema_version':1,'episode_id':10001},{'schema_version':1,'episode_id':1,'path':'/etc'},'invalid'])
def test_invalid_metadata_has_no_authority(tmp_path,value):
    (tmp_path/NAME).write_text(json.dumps(value))
    (tmp_path/'data').write_text('ok')
    assert listing(tmp_path)['items'][0]['episode_ids']==[]


def test_links_and_large_metadata_are_not_followed(tmp_path):
    root=tmp_path/'output';root.mkdir();outside=tmp_path/'outside';outside.mkdir()
    (outside/'secret').write_text('secret');(root/'link').symlink_to(outside,target_is_directory=True)
    assert not register(root/'link',1)
    assert not (outside/NAME).exists()
    (root/'data').write_text('ok');(root/NAME).symlink_to(outside/'secret')
    assert listing(root)['items'][0]['episode_ids']==[]
    (root/NAME).unlink();(root/NAME).write_text(' '*2048)
    assert listing(root)['items'][0]['episode_ids']==[]


def test_sdk_metadata_survives_business_error_without_explicit_artifacts(tmp_path,monkeypatch):
    from runner.sdk import EvaluationClient,run
    monkeypatch.delenv('MINI_EVAL_OUTPUT_DIR',raising=False)
    class Client(EvaluationClient):
        def run_episode(self,task,episode,context):
            (context.episode_output_dir/'partial.json').write_text('{}')
            raise RuntimeError('business failure')
    with pytest.raises(RuntimeError):run(Client(),{'episodes':1,'seed':0},tmp_path)
    assert listing(tmp_path,'episode-1')['items'][0]['episode_ids']==[1]


def test_metadata_write_failure_does_not_fail_business(tmp_path,monkeypatch):
    from runner.sdk import Context
    monkeypatch.setattr('runner.artifact_manifest.os.link',lambda *a,**kw:(_ for _ in ()).throw(OSError('disk full')))
    context=Context({},tmp_path,tmp_path/'episode-1',1)
    assert context.episode_output_dir==tmp_path/'episode-1'
    assert list(context.episode_output_dir.iterdir())==[]


@pytest.mark.parametrize('episode',[None,True,0,10001,'1'])
def test_registration_invalid_episode_never_writes(tmp_path,episode):
    assert not register(tmp_path,episode)
    assert not (tmp_path/NAME).exists()


def test_registration_refuses_relative_and_missing(tmp_path):
    assert not register(Path('relative'),1)
    assert not register(tmp_path/'missing',1)


def test_concurrent_registration_does_not_reassign_directory(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from runner.artifact_manifest import read
    import os
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes=list(pool.map(lambda i:register(tmp_path,i),range(1,9)))
    assert sum(outcomes)==1
    fd=os.open(tmp_path,os.O_RDONLY|os.O_DIRECTORY)
    try: assert outcomes[read(fd)-1]
    finally: os.close(fd)
    assert list(tmp_path.iterdir())==[tmp_path/NAME]


def test_unfinished_manifest_is_hidden_and_hardlinks_not_metadata(tmp_path):
    import os
    (tmp_path/(NAME+'-unfinished')).write_text('partial')
    target=tmp_path/'external';target.write_text('{"schema_version":1,"episode_id":5}')
    os.link(target,tmp_path/NAME)
    result=listing(tmp_path)
    assert [x['name'] for x in result['items']]==['external']
    assert result['items'][0]['episode_ids']==[]
    assert listing(tmp_path,NAME+'-unfinished')['status']=='invalid_path'


@pytest.mark.parametrize('mode',['host','docker'])
def test_legacy_collectors_skip_metadata_and_keep_business_files(tmp_path,monkeypatch,mode):
    from types import SimpleNamespace
    if mode=='host':
        from worker.host_backend import HostBackend
        out=tmp_path/'output';(out/'episode-1').mkdir(parents=True)
        (out/'episode-1'/'result.json').write_text('{}')
        (out/'episode-1'/NAME).write_text('{}')
        (out/'episode-1'/(NAME+'-pending')).write_text('{}')
        monkeypatch.setattr('worker.host_backend.execution_dirs',lambda *a:tmp_path)
        host=object.__new__(HostBackend);host.worker=SimpleNamespace(workspace=tmp_path)
        assert host.artifacts('execution')==[('episode-1/result.json',b'{}')]
    else:
        import io,tarfile
        from worker.artifacts import read_artifacts
        from runner.cs_contract import OUTPUT_DIR
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode='w') as archive:
            for name in ['episode-1/'+NAME,'episode-1/'+NAME+'-pending','episode-1/result.json']:
                item=tarfile.TarInfo(OUTPUT_DIR.rsplit('/',1)[-1]+'/'+name);item.size=2;archive.addfile(item,io.BytesIO(b'{}'))
        stream.seek(0)
        process=SimpleNamespace(stdout=stream,stderr=io.BytesIO(),wait=lambda **kw:0,poll=lambda:0,kill=lambda:None)
        monkeypatch.setattr('worker.artifacts.subprocess.Popen',lambda *a,**kw:process)
        assert read_artifacts('container')==[('episode-1/result.json',b'{}')]


@pytest.mark.parametrize('ordinary',[False,True])
@pytest.mark.parametrize('failed',[False,True])
def test_host_real_child_directory_registration(tmp_path,ordinary,failed):
    from backend.tests.test_host_shell_episode import client
    script=tmp_path/'business.py'
    script.write_text('import json,os,sys\nfrom pathlib import Path\n'
        'Path(os.environ["MINI_EVAL_OUTPUT_DIR"]).joinpath("result.json").write_text("{}")\n'
        + ('sys.exit(7)\n' if failed else 'print(json.dumps({"final":{"episode_count":1,"success_rate":1,"runtime":0.1}}))\n'))
    result,events,work,output=client(tmp_path,script,ordinary=ordinary,episodes=1)
    assert (result.returncode!=0)==failed
    assert listing(output,'episode-1')['items'][0]['episode_ids']==[1]


def test_nested_sdk_uses_outer_episode_identity(tmp_path,monkeypatch):
    from runner.sdk import EvaluationClient,EpisodeResult,run
    monkeypatch.delenv('MINI_EVAL_OUTPUT_DIR',raising=False)
    class Client(EvaluationClient):
        def run_episode(self,task,episode,context):
            (context.episode_output_dir/'result').write_text('ok')
            return EpisodeResult(True)
    run(Client(),{'episodes':1,'seed':0,'_parent_episode_id':7},tmp_path)
    assert listing(tmp_path)['items'][0]['episode_ids']==[7]
    assert not (tmp_path/'episode-1').exists()
