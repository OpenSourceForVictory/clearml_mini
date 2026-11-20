"""Large historical indices and real staging boundaries, not 10k business runs."""
import json
import sys
from pathlib import Path

import pytest
from runner.process_log_capture import encoded
from runner.process_log_run import run
from runner.process_log_store import Store, atomic, locked, read_json
from backend.tests.test_process_log_pipeline import setup_log
from backend.tests.test_process_log_failures import fixture as prepared
from worker.process_logs import LogService


def test_ten_thousand_scope_metadata_budget_and_business_fallback_preserve_artifacts(tmp_path, capsys):
    config = setup_log(tmp_path)
    first = Store(config, 'episode', 1)
    first.close({'incomplete': False, 'dropped_bytes': 0})
    with locked(config['root']) as descriptor:
        index = read_json(descriptor, 'manifest.json')
        empty = next(iter(index['scopes'].values()))
        index['scopes'] = {
            f"{config['execution_id']}/episode/{i}": {**empty, 'episode_id': i}
            for i in range(1, 10000)
        }
        atomic(descriptor, index)
    # Exercise the real writer against a 9999-scope closed history, rather than
    # producing gigabytes of repeated index fsyncs or claiming a throughput test.
    last = Store(config, 'episode', 10000)
    last.write(encoded({'sequence': 0, 'time': 'test', 'stream': 'stdout',
                        'text': 'last episode', 'gap': False}))
    last.close({'incomplete': False, 'dropped_bytes': 0})
    output = Path(config['root']).parent / 'output'
    output.mkdir()
    (output / 'result.json').write_text('{"success":true}')
    with locked(config['root']) as descriptor:
        index = read_json(descriptor, 'manifest.json')
        assert len(index['scopes']) == 10000
        assert index['scopes'][last.key]['next_sequence'] == 1
        # Reserve twice the serialized metadata; a lowered budget cannot be
        # silently exceeded or repaired by deleting business output.
        index['policy'].update(segment_bytes=65536, scope_bytes=131072)
        index['policy']['execution_bytes'] = len(json.dumps(index, separators=(',', ':')).encode()) * 8 - 1
        atomic(descriptor, index)
    with pytest.raises(ValueError, match='metadata budget exhausted'):
        Store(config, 'business')
    code = run([sys.executable, '-c', "print('business still runs')"],
               config={'_process_logs': config}, scope='business')
    assert code == 0
    assert 'business still runs' in capsys.readouterr().out
    assert (output / 'result.json').read_text() == '{"success":true}'
    with locked(config['root']) as descriptor:
        assert len(read_json(descriptor, 'manifest.json')['scopes']) == 10000


def test_staged_execution_is_reported_cleaned_without_following_staging_or_replacement(tmp_path):
    worker, task, config, registry_path, item, calls = prepared(tmp_path)
    service = LogService(worker)
    service.sync(registry_path, item)
    root = Path(config['root'])
    run_root = root.parent
    staging = run_root.parent / '.cleanup'
    staging.mkdir()
    run_root.rename(staging / run_root.name)
    # Staged bytes remain on disk, but are no longer exposed as available logs.
    service.sync(registry_path, item)
    assert not registry_path.exists()
    assert calls[-1][1]['status'] == 'cleaned'
    assert (staging / run_root.name / 'logs').is_dir()
    root.mkdir(parents=True)
    (root / 'replacement').write_text('must not remove')
    with pytest.raises(ValueError, match='root identity changed'):
        service.read(item, {'scope': 'task', 'episode_id': 0, 'generation': config['generation'], 'query': {}})
    assert (root / 'replacement').read_text() == 'must not remove'
