"""Version 1 client-side evaluation contract; standard library only."""
import ipaddress
import re
from urllib.parse import urlsplit

VERSION = 1
INPUT_PATH = '/tmp/mini-eval-job.json'
OUTPUT_DIR = '/tmp/mini-eval-output'
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_ARTIFACT_TOTAL = 32 * 1024 * 1024
MAX_ARTIFACT_COUNT = 8


def check_ip(host):
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    ip = getattr(ip, 'ipv4_mapped', None) or ip
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        raise ValueError('endpoint must address a reachable external inference service')


def endpoint_parts(value):
    if not isinstance(value, str) or len(value) > 2000 or any(ord(c) < 33 for c in value) or '\\' in value:
        raise ValueError('invalid inference endpoint')
    parts = urlsplit(value)
    if (parts.scheme not in ('http', 'https', 'ws', 'wss') or not parts.hostname
            or parts.username is not None or parts.password is not None or parts.query or parts.fragment):
        raise ValueError('endpoint requires HTTP(S)/WS(S), without credentials, query or fragment')
    if parts.hostname.lower().rstrip('.') == 'localhost':
        raise ValueError('localhost inside the container is not the inference server')
    check_ip(parts.hostname)
    port = parts.port if parts.port is not None else (443 if parts.scheme in ('https', 'wss') else 80)
    if not 1 <= port <= 65535:
        raise ValueError('invalid inference service port')
    return parts, port


def artifact_name(name):
    # Portable relative names only; paths are metadata and never extracted on the host.
    return bool(isinstance(name, str) and len(name) <= 180 and
                re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', name) and
                all(x not in ('.', '..') for x in name.split('/')))
