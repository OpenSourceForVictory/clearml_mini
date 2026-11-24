"""Export declared body constraints and confirmation classes without reading a DB."""
import json
import os

from fastapi.routing import APIRoute
from pydantic import TypeAdapter

from backend.app.priority_routes import category_for, ROLES


CONFIRMATION = {
    'health':'read-only liveness/readiness',
    'execution_control':'state/lease confirmation; retry original execution identity',
    'user_action':'mutation/auth response; preserve endpoint idempotency or revision contract',
    'user_long':'preflight or external operation; see endpoint-specific revision/confirmation',
    'page_read':'read response; retry refresh, do not infer mutation completion',
    'worker_claim':'claim/config command; preserve execution or command identity',
    'worker_result':'reliable result acknowledgement; duplicate content must match',
    'worker_observation':'latest sample plus config/event acknowledgement; do not discard whole packet',
    'bulk_transfer':'endpoint-specific sequence/content acknowledgement or authorized byte stream',
}


def collect(app):
    bodies, routes = {}, []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        params = []
        for field in route.dependant.body_params:
            key = str(field.type_)
            bodies[key] = TypeAdapter(field.type_).json_schema()
            params.append({'name':field.name, 'required':field.required, 'schema':key})
        for method in sorted(route.methods):
            category = category_for(route.path, method)
            routes.append({'method':method, 'path':route.path, 'category':category,
                           'role':ROLES[category], 'body':params, 'confirmation':CONFIRMATION[category],
                           'source':route.endpoint.__module__+'.'+route.endpoint.__name__})
    return {'scope':'Declared Pydantic constraints only; custom validators and response/transaction rules remain in source and the manual audit.',
            'gateway_body_byte_limit':None, 'routes':routes, 'body_schemas':bodies}


def main():
    os.environ.setdefault('JWT_SECRET', 'route-audit-no-login-no-network')
    from backend.app.main import app
    print(json.dumps(collect(app), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
