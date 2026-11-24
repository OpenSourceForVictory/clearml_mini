from pathlib import Path
import re

import pytest
import yaml
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.db_budget import Settings
from backend.app.core import ClientTemplate
from backend.tests.control_plane_load import api_log_names, pool_profiles, render_gateway_config, render_priority_gateway_config, seed
from backend.app.priority_runtime import ROLE_POOLS


def test_split_gateway_uses_distinct_loopback_backends():
    template = (Path(__file__).resolve().parents[2] / 'deploy/split-api-nginx.conf').read_text()
    config = render_gateway_config(template, 18001, 18002, 18003)
    assert 'listen 127.0.0.1:18003;' in config
    assert 'set $ordinary_backend 127.0.0.1:18001;' in config
    assert 'set $control_backend 127.0.0.1:18002;' in config
    assert 'control-127.0.0.1' not in config
    assert 'control-api:8000' not in config
    assert api_log_names(5, True) == ['api-ordinary-5.log', 'api-control-5.log']
    assert api_log_names(5, False) == ['api-5.log']


def test_mixed_load_seed_adds_published_command_template_without_changing_worker_count():
    engine = create_engine('sqlite://')
    identities = seed(engine, 2, mixed_ui=True)
    with Session(engine) as session:
        templates = session.scalars(select(ClientTemplate)).all()
    assert len(identities) == 2
    assert [(t.id, t.task_type, t.status) for t in templates] == [
        ('load-template', 'command', 'published')]
    engine.dispose()


def test_split_gateway_rejects_incomplete_template():
    with pytest.raises(ValueError, match='missing a required route marker'):
        render_gateway_config('listen 8000; set $ordinary_backend api:8000;', 18001, 18002, 18003)
    with pytest.raises(ValueError, match='missing a required route marker'):
        render_gateway_config('listen 8000; set $control_backend control-api:8000;', 18001, 18002, 18003)


def test_priority_load_uses_four_distinct_backends_and_health_target():
    ports = {role:18010+i for i,role in enumerate(ROLE_POOLS)}
    config = render_priority_gateway_config(ports, 18000)
    assert 'listen 127.0.0.1:18000;' in config
    for role, port in ports.items():
        assert role+'-api:8000' not in config
        assert f'127.0.0.1:{port}' in config
    assert f'set $interaction_backend 127.0.0.1:{ports["interaction"]};' in config
    assert api_log_names(100, False, True) == [f'api-{role}-100.log' for role in ROLE_POOLS]
    with pytest.raises(ValueError, match='every role'):
        render_priority_gateway_config({}, 18000)


def test_role_aware_pools_preserve_control_and_observation_capacity():
    profiles = pool_profiles(10, 8, 4, split_api=True, role_aware=True)
    assert profiles['ordinary'] == {'ordinary': 10, 'observation': 4, 'result': 3, 'scheduler': 1}
    assert profiles['control'] == {'control': 8}
    assert sum(sum(profile.values()) for profile in profiles.values()) == 26
    stopped = pool_profiles(10, 8, 4, split_api=True, role_aware=True, scheduler_enabled=False)
    assert 'scheduler' not in stopped['ordinary']
    assert sum(sum(profile.values()) for profile in stopped.values()) == 25
    with pytest.raises(ValueError, match='require split API'):
        pool_profiles(10, 8, 4, role_aware=True)


def test_split_default_matches_40_threads_per_process():
    profiles = pool_profiles(23, 40, 9, result=7, split_api=True, role_aware=True)
    assert profiles == {'ordinary': {'ordinary': 23, 'observation': 9, 'result': 7, 'scheduler': 1},
                        'control': {'control': 40}}
    assert {role: sum(sizes.values()) for role, sizes in profiles.items()} == {'ordinary': 40, 'control': 40}


def test_legacy_split_load_uses_full_pools_in_both_processes():
    profiles = pool_profiles(10, 5, 2, split_api=True, scheduler_enabled=False)
    assert profiles == {'ordinary': {'ordinary': 10, 'control': 5, 'observation': 2,
                                     'result': 2, 'scheduler': 1},
                        'control': {'ordinary': 10, 'control': 5, 'observation': 2,
                                    'result': 2, 'scheduler': 1}}


def test_compose_split_defaults_match_role_specific_40_connection_budget():
    class ComposeLoader(yaml.SafeLoader):
        pass

    ComposeLoader.add_constructor('!reset', lambda loader, node: loader.construct_sequence(node))
    overlay = yaml.load((Path(__file__).resolve().parents[2] / 'deploy/split-api-compose.yml').read_text(),
                        Loader=ComposeLoader)['services']
    profiles = {}
    for service, role in (('api', 'ordinary'), ('control-api', 'control')):
        env = overlay[service]['environment']
        sizes = {}
        for key, attr in (('DB_POOL_SIZE', 'ordinary'), ('DB_CONTROL_POOL_SIZE', 'control'),
                          ('DB_OBSERVATION_POOL_SIZE', 'observation'), ('DB_RESULT_POOL_SIZE', 'result'),
                          ('DB_SCHEDULER_POOL_SIZE', 'scheduler')):
            value = str(env[key])
            match = re.fullmatch(r'\$\{DB_SPLIT_[A-Z_]+:-([0-9]+)\}', value)
            sizes[attr] = int(match.group(1) if match else value)
        settings = Settings(**sizes, route_group=role, processes=int(env['API_DB_PROCESS_COUNT']),
                            scheduler_enabled=env.get('SCHEDULER_ENABLED', '1') == '1')
        assert env['API_ROUTE_GROUP'] == role
        assert env['DB_ADMISSION_ENABLED'] == '0'
        profiles[role] = settings.active_pool_sizes
    assert profiles == pool_profiles(23, 40, 9, result=7, split_api=True, role_aware=True)
    assert {role: sum(sizes.values()) for role, sizes in profiles.items()} == {'ordinary': 40, 'control': 40}


@pytest.mark.parametrize('kind,code,outage,accepted', [
    ('logs','502',True,True), ('artifact','504',True,True),
    ('logs','502',False,False), ('heartbeat','502',True,False),
    ('ui_control','504',True,False), ('result','500',True,False),
    ('logs','exception:TimeoutError',True,False), ('heartbeat','503',True,True),
])
def test_fault_acceptance_never_hides_control_errors(kind, code, outage, accepted):
    from backend.tests.control_plane_load import acceptable_responses
    assert acceptable_responses({kind:{code:1}}, mixed_ui=True, bulk_outage=outage) is accepted


@pytest.mark.parametrize('arguments', [
    ['--bulk-outage-seconds','-1'],
    ['--bulk-outage-seconds','15'],
    ['--priority-api','--bulk-outage-seconds','15'],
    ['--priority-api','--independent-logs','--seconds','60','--bulk-outage-seconds','15'],
])
def test_fault_arguments_reject_unsupported_runs_before_database_access(monkeypatch, capsys, arguments):
    from backend.tests.control_plane_load import main
    monkeypatch.setattr('sys.argv',['load','--output','/unused',*arguments])
    monkeypatch.delenv('LOG_TEST_DATABASE_URL',raising=False)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert '--bulk-outage-seconds requires' in capsys.readouterr().err


@pytest.mark.parametrize('size',[1,1024,16384])
def test_varied_logs_have_exact_size_and_stable_retry_content(size):
    from backend.tests.control_plane_load import log_payload
    value = log_payload('node-1',3,size,True)
    assert len(value.encode()) == size
    assert value == log_payload('node-1',3,size,True)
    assert log_payload('node-1',3,size) == 'x'*size
    if size >= 1024:
        import zlib
        assert value != log_payload('node-1',4,size,True)
        assert len(zlib.compress(value.encode())) > size/4
