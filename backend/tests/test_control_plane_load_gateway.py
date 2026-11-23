from pathlib import Path

import pytest

from backend.tests.control_plane_load import api_log_names, pool_profiles, render_gateway_config


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


def test_split_gateway_rejects_incomplete_template():
    with pytest.raises(ValueError, match='missing a required route marker'):
        render_gateway_config('listen 8000; set $ordinary_backend api:8000;', 18001, 18002, 18003)
    with pytest.raises(ValueError, match='missing a required route marker'):
        render_gateway_config('listen 8000; set $control_backend control-api:8000;', 18001, 18002, 18003)


def test_role_aware_pools_preserve_control_and_observation_capacity():
    profiles = pool_profiles(10, 8, 4, split_api=True, role_aware=True)
    assert profiles['ordinary'] == {'ordinary': 10, 'control': 1, 'observation': 4,
                                    'result': 3, 'scheduler': 1}
    assert profiles['control'] == {'ordinary': 1, 'control': 8, 'observation': 1,
                                   'result': 1, 'scheduler': 1}
    assert sum(sum(profile.values()) for profile in profiles.values()) == 31
    with pytest.raises(ValueError, match='require split API'):
        pool_profiles(10, 8, 4, role_aware=True)
