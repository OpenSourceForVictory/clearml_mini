"""Pure endpoint selection shared by admission, diagnostics and historical views."""
import ipaddress
from runner.cs_contract import endpoint_parts


def resource_key(url):
    parts, port = endpoint_parts(url)
    host = parts.hostname.lower().rstrip('.')
    try:
        address = ipaddress.ip_address(host)
        host = str(getattr(address, 'ipv4_mapped', None) or address)
    except ValueError:
        pass
    return host, port


def candidates(spec,s=None):
    if not spec or spec.get('workload') == 'service':
        return []
    if spec.get('service_mode')=='none':return []
    if spec.get('service_mode')=='managed':
        from .service_dependencies import candidates as managed_candidates
        return managed_candidates(s,spec)
    if spec.get('endpoints'):
        return spec['endpoints']
    return [{'url': spec['endpoint'], 'mode': 'exclusive' if spec['template'].get('endpoint_exclusive', False) else 'shared'}]


def assigned(run, spec=None):
    if (spec or run.job.client_spec or {}).get('workload') == 'service': return None
    if run.assigned_endpoint:
        return {'url': run.assigned_endpoint, 'mode': run.assigned_endpoint_mode}
    spec = spec if spec is not None else run.job.client_spec
    # Compatibility for old executions only; never guess the first list entry.
    if spec and spec.get('endpoint') and run.status != 'PENDING' and run.execution_id and (run.status != 'CANCELLED' or run.started_at):
        return candidates(spec)[0]
    return None


def holdings(runs):
    return [value for run in runs if (value := assigned(run)) is not None]


def inspect(spec, held, s=None):
    result = []
    for candidate in candidates(spec,s):
        matches = [other for other in held if resource_key(other['url']) == resource_key(candidate['url'])]
        blocked = bool(matches) and (candidate['mode'] == 'exclusive' or any(x['mode'] == 'exclusive' for x in matches))
        result.append({**candidate, 'available': not blocked, 'occupancy': len(matches),
                       'reason': 'endpoint_busy' if blocked else None})
    return result


def choose(spec, held, s=None):
    available = [item for item in inspect(spec, held,s) if item['available']]
    return min(available, key=lambda item: item['occupancy']) if available else None


def input_spec(run, spec):
    if spec.get('workload') == 'service' or spec.get('service_mode')=='none': return dict(spec)
    selected = assigned(run, spec)
    if not selected:
        raise ValueError('execution has no assigned inference endpoint')
    return {**{k: v for k, v in spec.items() if k not in ('endpoint', 'endpoints')}, 'endpoint': selected['url'],
            'service_key':getattr(run,'assigned_service_key',None) or 'default'}


def view(run, spec):
    selection = assigned(run, spec)
    return {'endpoint': selection['url'] if selection else None,
            'service_instance_id':getattr(run,'assigned_service_instance_id',None),
            'service_execution_id':getattr(run,'assigned_service_execution_id',None),
            'service_key':getattr(run,'assigned_service_key',None),
            'waiting_service':getattr(run,'waiting_service',False),
            'endpoint_mode': selection['mode'] if selection else None,
            'endpoint_candidates': candidates(spec)}
