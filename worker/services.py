"""Local service binding validation and bounded health probes."""
import socket
import urllib.request
import urllib.error


def is_service(task):return task.get('benchmark')=='inference-service'


def check_bind(service):
    # Fails if an address is not local or another program already holds this port.
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((service['address'],service['port']))
    except OSError as exc:
        raise RuntimeError('service address is not local or port is already in use') from exc


def health(service):
    try:
        if service['health_type']=='tcp':
            with socket.create_connection((service['address'],service['port']),timeout=1):pass
        else:
            # No redirects: readiness must be demonstrated by this local instance.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self,*args,**kwargs):return None
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
            scheme='https' if service.get('protocol') in ('https','wss') else 'http'
            with opener.open(f"{scheme}://{service['address']}:{service['port']}{service['health_path']}",timeout=1) as r:
                if not 200<=r.status<300:return {'ready':False,'error':'health endpoint returned a non-success status'}
        return {'ready':True,'error':None}
    except (OSError,ValueError,urllib.error.URLError):return {'ready':False,'error':'service health probe failed'}
