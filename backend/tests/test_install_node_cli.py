"""Installer dispatch tests: no real accounts, units, or privileged filesystem writes."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import install_node


@pytest.mark.parametrize('mode',['root','existing','user','bad-home','bad-services','existing-config','value-error','os-error'])
def test_installer_dispatch_and_safe_failures(tmp_path,monkeypatch,capsys,mode):
    from scripts import install_system_node
    home=tmp_path/'home';home.mkdir();root=tmp_path/'bundle';root.mkdir()
    (root/'release.sha256').write_text('release-digest')
    credential=tmp_path/'credential.json';credential.write_text(json.dumps({'node_name':'n','token':'private-worker-token'}))
    monkeypatch.setattr(install_node,'ROOT',root);monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    monkeypatch.setattr(install_node.os,'geteuid',lambda:0 if mode=='root' else 1000)
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE','0')
    monkeypatch.setenv('XDG_RUNTIME_DIR','/run/user/1000');monkeypatch.setenv('DBUS_SESSION_BUS_ADDRESS','unix:path=/run/user/1000/bus')
    args=['install','--credential',str(credential)]
    if mode=='existing':args+=['--existing','--home',str(home),'--service','user:worker.service']
    if mode=='bad-home':args+=['--home',str(tmp_path/'other')]
    if mode=='bad-services':args+=['--service','user:worker.service']
    monkeypatch.setattr(sys,'argv',args)
    app=home/'.local/share/mini-eval-worker';app.mkdir(parents=True)
    config=home/'.config/mini-eval';config.mkdir(parents=True)
    if mode in ('existing','existing-config'):(config/'gpu-gpu0.json').write_text('{"state_path":"/state"}')
    calls=[]
    def run(argv,**kwargs):
        calls.append(('install-worker',argv));(config/'gpu-gpu0.json').write_text('{"state_path":"/state"}')
        if mode=='os-error':raise OSError('private-worker-token')
        if mode=='value-error':raise ValueError('layout rejected')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(install_node.subprocess,'run',run)
    monkeypatch.setattr(install_node,'replace_existing',lambda layout,c: calls.append(('replace',layout,c)))
    monkeypatch.setattr(install_system_node,'install',lambda *a:calls.append(('root',a)))
    monkeypatch.setattr(install_node,'install_updater',lambda *a:calls.append(('updater',a)))
    monkeypatch.setattr(install_node,'Updater',lambda layout:SimpleNamespace(control=lambda action:calls.append((action,layout))))
    rejected=mode in ('bad-home','bad-services','existing-config','value-error','os-error')
    if rejected:
        with pytest.raises(SystemExit) as error:install_node.main()
        assert error.value.code==1
    else:install_node.main()
    output=capsys.readouterr()
    assert 'private-worker-token' not in output.out+output.err
    if mode=='root':assert calls[0][0]=='root' and len(calls)==1
    elif mode=='existing':assert calls[0][0]=='replace' and len(calls)==1
    elif mode=='user':
        assert [x[0] for x in calls]==['install-worker','updater','enable','start']
        assert json.loads((app/'.worker-release.json').read_text())['sha256']=='release-digest'
    elif mode in ('bad-home','bad-services','existing-config'):assert not calls


@pytest.mark.parametrize('mode',['empty','no-services','mixed','valid'])
def test_existing_layout_requires_complete_consistent_services(tmp_path,mode):
    directory=tmp_path/'.config/mini-eval';directory.mkdir(parents=True)
    (directory/'metadata.json').write_text('{}')
    if mode!='empty':(directory/'gpu0.json').write_text('{"state_path":"/tmp/state"}')
    services=[] if mode=='no-services' else ['user:worker.service','system:other.service'] if mode=='mixed' else ['user:worker.service']
    if mode=='valid':
        layout=install_node.existing_layout(tmp_path,services)
        assert layout['config_paths']==[str(directory/'gpu0.json')]
        assert layout['services']==[{'scope':'user','name':'worker.service'}]
    else:
        with pytest.raises(ValueError):install_node.existing_layout(tmp_path,services)


@pytest.mark.parametrize('case',['valid','stale','without-existing','service-override'])
def test_checked_snapshot_cli(tmp_path,monkeypatch,case):
    credential=tmp_path/'credential.json';credential.write_text('{"node_name":"n"}')
    snapshot={'layout':{'app_dir':'/original'},'fingerprint':'checked'}
    target=tmp_path/'layout.json';target.write_text(json.dumps(snapshot))
    args=['install','--credential',str(credential),'--layout-snapshot',str(target)]
    if case!='without-existing':args.append('--existing')
    if case=='service-override':args+=['--service','system:mini-eval-worker-other.service']
    monkeypatch.setattr(sys,'argv',args)
    monkeypatch.setattr('scripts.node_layout.discover',lambda *a:None if case=='stale' else snapshot)
    seen=[]
    monkeypatch.setattr(install_node,'replace_existing',lambda *a,**kw:seen.append((a,kw)))
    if case=='valid':
        install_node.main()
        assert seen[0][0][0]==snapshot['layout'] and seen[0][1]['checked_snapshot']==snapshot
    else:
        with pytest.raises(SystemExit):install_node.main()
        assert not seen
