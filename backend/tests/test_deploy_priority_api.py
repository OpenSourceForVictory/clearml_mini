from argparse import Namespace
import json
from pathlib import Path

import pytest

from scripts import deploy_priority_api as deployment


IMAGE_CONFIG = json.dumps({'services': {name: {'image': 'mini-eval-api:dev'} for name in (
    'api', 'migrate', 'bootstrap', 'execution-control-api', 'worker-data-api', 'bulk-transfer-api')}})


def options(tmp_path):
    env = tmp_path / '.env'
    env.write_text('PLACEHOLDER=1\n')
    return Namespace(env_file=env, project='mini-eval', previous_overlay=[], overlay=[],
                     image_mode='build', backup_dir=tmp_path / 'backups', drained=True)


def test_missing_drain_and_missing_env_stop_before_compose(tmp_path, monkeypatch):
    args = options(tmp_path)
    monkeypatch.setattr(deployment, 'compose', lambda: pytest.fail('compose must not run'))
    args.drained = False
    with pytest.raises(RuntimeError, match='Drain tasks'):
        deployment.deploy(args)
    args.drained = True
    args.env_file.unlink()
    with pytest.raises(RuntimeError, match='Environment file'):
        deployment.deploy(args)


def test_reject_split_overlay_and_unversioned_pull(tmp_path, monkeypatch):
    args = options(tmp_path)
    args.overlay = [Path('deploy/split-api-compose.yml')]
    with pytest.raises(RuntimeError, match='split-API'):
        deployment.deploy(args)
    args.overlay = []
    args.image_mode = 'pull'
    with pytest.raises(RuntimeError, match='versioned'):
        deployment.deploy(args)


def test_active_execution_never_stops_api(tmp_path, monkeypatch):
    args = options(tmp_path)
    calls = []
    monkeypatch.setattr(deployment, 'compose', lambda: ['docker', 'compose'])

    def fake(command, **kwargs):
        calls.append(command)
        if '--format' in command:
            return IMAGE_CONFIG
        if 'ps' in command and '--services' in command:
            return 'api\ndb'
        if 'heads' in command:
            return 'c14d09e (head)'
        if 'psql' in command:
            return '1'
        return ''

    monkeypatch.setattr(deployment, 'run', fake)
    with pytest.raises(RuntimeError, match='active/unconfirmed'):
        deployment.deploy(args)
    assert not any('stop' in command for command in calls)
    assert not args.backup_dir.exists()


def test_deploy_orders_backup_migration_and_start(tmp_path, monkeypatch):
    args = options(tmp_path)
    calls = []
    monkeypatch.setattr(deployment, 'compose', lambda: ['docker', 'compose'])

    def fake(command, **kwargs):
        calls.append(command)
        if '--format' in command:
            return IMAGE_CONFIG
        if 'ps' in command and '--services' in command:
            return 'api\ndb'
        if 'heads' in command or 'current' in command:
            return 'c14d09e (head)'
        if 'SHOW max_connections' in command:
            return '200'
        if 'psql' in command:
            return '0'
        if 'pg_dump' in command:
            kwargs['stdout'].write(b'valid-backup')
        return ''

    monkeypatch.setattr(deployment, 'run', fake)
    deployment.deploy(args)
    ops = [' '.join(command) for command in calls]
    stop = next(i for i, op in enumerate(ops) if ' stop api' in op)
    backup = next(i for i, op in enumerate(ops) if ' pg_dump ' in op)
    db_up = next(i for i, op in enumerate(ops) if ' up -d --no-deps --wait db' in op)
    migrate = next(i for i, op in enumerate(ops) if ' run --rm --no-deps migrate' in op and 'alembic' not in op)
    start = next(i for i, op in enumerate(ops) if ' up -d --no-build --wait --remove-orphans' in op)
    assert backup < stop < db_up < migrate < start
    assert (args.backup_dir / 'mini-eval-before-c14d09e.dump').read_bytes() == b'valid-backup'


def test_bad_backup_is_removed_and_new_api_does_not_start(tmp_path, monkeypatch):
    args = options(tmp_path)
    calls = []
    monkeypatch.setattr(deployment, 'compose', lambda: ['docker', 'compose'])

    def fake(command, **kwargs):
        calls.append(command)
        if '--format' in command:
            return IMAGE_CONFIG
        if 'ps' in command and '--services' in command:
            return 'api\ndb'
        if 'heads' in command:
            return 'c14d09e (head)'
        if 'psql' in command:
            return '0'
        if 'pg_dump' in command:
            kwargs['stdout'].write(b'incomplete')
        if 'pg_restore' in command:
            raise RuntimeError('invalid archive')
        return ''

    monkeypatch.setattr(deployment, 'run', fake)
    with pytest.raises(RuntimeError, match='invalid archive'):
        deployment.deploy(args)
    assert list(args.backup_dir.iterdir()) == []
    assert not any('up' in command for command in calls)


@pytest.mark.parametrize('maximum,current,expected', [
    ('100', 'c14d09e (head)', 'max_connections'),
    ('200', 'older123', 'Migration mismatch'),
])
def test_capacity_or_migration_failure_blocks_new_api(tmp_path, monkeypatch, maximum, current, expected):
    args = options(tmp_path)
    calls = []
    monkeypatch.setattr(deployment, 'compose', lambda: ['docker', 'compose'])

    def fake(command, **kwargs):
        calls.append(command)
        if '--format' in command:
            return IMAGE_CONFIG
        if 'ps' in command and '--services' in command:
            return 'api\ndb'
        if 'heads' in command:
            return 'c14d09e (head)'
        if 'current' in command:
            return current
        if 'SHOW max_connections' in command:
            return maximum
        if 'psql' in command:
            return '0'
        if 'pg_dump' in command:
            kwargs['stdout'].write(b'valid-backup')
        return ''

    monkeypatch.setattr(deployment, 'run', fake)
    with pytest.raises(RuntimeError, match=expected):
        deployment.deploy(args)
    assert not any('--remove-orphans' in command for command in calls)
    assert (args.backup_dir / 'mini-eval-before-c14d09e.dump').exists()


def test_existing_backup_is_not_overwritten_or_stopped(tmp_path, monkeypatch):
    args = options(tmp_path)
    args.backup_dir.mkdir()
    backup = args.backup_dir / 'mini-eval-before-c14d09e.dump'
    backup.write_bytes(b'previous')
    calls = []
    monkeypatch.setattr(deployment, 'compose', lambda: ['docker', 'compose'])

    def fake(command, **_kwargs):
        calls.append(command)
        if '--format' in command:
            return IMAGE_CONFIG
        if 'ps' in command and '--services' in command:
            return 'api\ndb'
        if 'heads' in command:
            return 'c14d09e (head)'
        if 'psql' in command:
            return '0'
        return ''

    monkeypatch.setattr(deployment, 'run', fake)
    with pytest.raises(RuntimeError, match='already exists'):
        deployment.deploy(args)
    assert backup.read_bytes() == b'previous'
    assert not any('stop' in command for command in calls)


@pytest.mark.parametrize('output', ['', 'c1 (head)\nc2 (head)'])
def test_revision_requires_single_head(output):
    with pytest.raises(RuntimeError, match='one Alembic revision'):
        deployment.revision(output)


def test_mismatched_images_are_rejected():
    config = json.loads(IMAGE_CONFIG)
    config['services']['migrate']['image'] = 'mini-eval-api:old'
    with pytest.raises(RuntimeError, match='same target image'):
        deployment.verify_images(json.dumps(config))


def test_cli_passes_existing_configuration(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr(deployment, 'deploy', captured.append)
    deployment.main(['--drained', '--env-file', str(tmp_path / '.env'), '--project', 'existing',
                     '--previous-overlay', 'deploy/split-api-compose.yml',
                     '--overlay', 'deploy/split-api-lan-compose.yml'])
    assert captured[0].project == 'existing'
    assert captured[0].drained
    assert captured[0].previous_overlay == [Path('deploy/split-api-compose.yml')]
    assert captured[0].overlay == [Path('deploy/split-api-lan-compose.yml')]


def test_cli_exits_on_failed_preflight(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        deployment.main(['--drained', '--env-file', str(tmp_path / 'missing.env')])
    assert error.value.code == 1
    assert 'Environment file does not exist' in capsys.readouterr().err
