from pathlib import Path
import re

from backend.app.priority_gateway import BACKENDS, _route_pattern, render
from backend.app.priority_routes import ROLES, manifest, role_for


def test_checked_in_gateway_is_generated_from_route_manifest():
    config = (Path(__file__).resolve().parents[2] / 'deploy/priority-api-nginx.conf').read_text()
    assert config == render()
    assert config.count('default interaction-api:8000;') == 1
    assert config.count('proxy_pass http://$priority_backend$request_uri;') == 1
    assert sum(line.startswith('    GET:') or line.startswith('    POST:') or
               line.startswith('    PUT:') or line.startswith('    PATCH:') or
               line.startswith('    DELETE:') or line.startswith('    HEAD:') or
               line.startswith('    ~^') for line in config.splitlines()) == len(manifest())


def test_gateway_map_keeps_method_and_dynamic_path_boundaries():
    config = render()
    for method, path, category, _, _ in manifest():
        backend = BACKENDS[ROLES[category] or 'interaction']
        if '{' in path:
            rule = _route_pattern(method, path)
            example = re.sub(r'\{[^}]+\}', 'sample-id', path)
            assert re.fullmatch(rule[1:], method+':'+example)
        else:
            rule = method+':'+path
        assert f'    {rule} {backend};' in config
        assert role_for(re.sub(r'\{[^}]+\}', 'sample-id', path), method) == ROLES[category]
    assert not re.fullmatch(_route_pattern('POST', '/api/v1/worker/slots/{id}/claim')[1:],
                            'GET:/api/v1/worker/slots/x/claim')
    assert config.index('GET:/api/v1/suites/catalogs') < config.index('~^GET:/api/v1/suites/[^/]+$')


def test_gateway_unknown_paths_use_interaction_default():
    assert 'default interaction-api:8000;' in render()
    assert role_for('/api/v1/unknown', 'POST') == 'interaction'
