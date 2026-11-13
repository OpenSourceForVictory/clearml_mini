"""Authenticated bounded requests, with redirects disabled."""
import json
import urllib.request
import urllib.error


class Superseded(RuntimeError): pass


class Transport:
    def __init__(self, api_url, token):
        if not api_url.startswith(('http://', 'https://')): raise ValueError('invalid platform URL')
        self.url, self.token = api_url.rstrip('/')+'/api/v1/agent/', token
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **kw): return None
        self.opener = urllib.request.build_opener(NoRedirect())

    def post(self, path, data):
        request = urllib.request.Request(self.url+path, data=json.dumps(data).encode(),
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer '+self.token})
        try:
            with self.opener.open(request, timeout=5) as r:
                payload = r.read(131073)
                if len(payload) > 131072: raise ValueError('Agent response too large')
                return json.loads(payload)
        except urllib.error.HTTPError as e:
            if e.code in (401, 409): raise Superseded('Agent credential/session invalid') from None
            raise RuntimeError('Agent request failed') from None
