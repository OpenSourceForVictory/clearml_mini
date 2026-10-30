import json
from types import SimpleNamespace
import pytest
from scripts.install_worker import discover, identity, plan


def test_multigpu_selection_and_independent_paths(tmp_path, monkeypatch):
    monkeypatch.setattr('scripts.install_worker.subprocess.run', lambda *a, **k: SimpleNamespace(stdout='0\n2\n'))
    assert discover({}, 'all') == [0, 2]
    for bad in ('1', '0,0', '-1'):
        with pytest.raises(ValueError):
            discover({}, bad)
    with pytest.raises(ValueError):
        discover({'mock': True}, 'all')
    values = plan({'workspace_root': '/srv/work'}, 'node', 'all', [0, 2], tmp_path)
    assert len({v['state_path'] for _, _, v in values}) == 2
    assert values[0][2]['workspace_root'] != values[1][2]['workspace_root']
    assert plan({}, 'old', None, [0], tmp_path)[0][0] == 'old'


def test_no_clobber_and_update_preserves_state(tmp_path):
    target = tmp_path/'.config/mini-eval/gpu0.json'
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({'node_name': 'host', 'state_path': '/previous/state.json'}))
    with pytest.raises(ValueError, match='already exists'):
        plan({'node_name': 'host'}, 'gpu0', None, [0], tmp_path)
    assert plan({'node_name': 'host'}, 'gpu0', None, [0], tmp_path, True)[0][2]['state_path'] == '/previous/state.json'
    with pytest.raises(ValueError, match='identity'):
        plan({'node_name': 'other'}, 'gpu0', None, [0], tmp_path, True)


def test_readonly_auth_failure_does_not_expose_credential(monkeypatch):
    import urllib.error
    config = {'api_url': 'http://localhost', 'token': 'private-token', 'node_name': 'host'}
    def fail(*a, **k):
        assert a[0].get_method() == 'GET'
        raise urllib.error.HTTPError('url', 401, 'secret', {}, None)
    monkeypatch.setattr('scripts.install_worker.urllib.request.urlopen', fail)
    with pytest.raises(ValueError, match='HTTP 401') as e:
        identity(config)
    assert config['token'] not in str(e.value)
    monkeypatch.setattr('scripts.install_worker.urllib.request.urlopen', lambda *a, **k: (_ for _ in ()).throw(OSError('secret')))
    with pytest.raises(ValueError, match='unreachable'):
        identity(config)


def test_check_is_readonly_and_install_rejects_live_instances(tmp_path, monkeypatch, capsys):
    import fcntl
    from scripts.install_worker import main
    config = tmp_path/'input.json'
    config.write_text(json.dumps({'api_url':'http://test','token':'private','node_name':'host','runner_image':'runner','mock':True}))
    monkeypatch.setattr('scripts.install_worker.Path.home', lambda:tmp_path)
    monkeypatch.setattr('scripts.install_worker.identity', lambda c:None)
    monkeypatch.setattr('scripts.install_worker.subprocess.run', lambda *a,**k:None)
    monkeypatch.setattr('sys.argv',['installer','--config',str(config),'--check'])
    main()
    assert not (tmp_path/'.config').exists()
    assert 'private' not in capsys.readouterr().out
    conf = tmp_path/'.config/mini-eval';conf.mkdir(parents=True)
    state = tmp_path/'state.json';state.write_text('null')
    (conf/'other.json').write_text(json.dumps({'state_path':str(state)}))
    with open(str(state)+'.lock','a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        monkeypatch.setattr('sys.argv',['installer','--config',str(config)])
        with pytest.raises(SystemExit):
            main()
    assert not (conf/'gpu0.json').exists()
