"""Bounded, secret-safe input contract shared by API and independent executor."""
import re
from urllib.parse import urlsplit

class Rejected(ValueError):
    pass


def validate(raw):
    if not isinstance(raw,dict): raise Rejected('invalid onboarding form')
    allowed={'host','port','username','fingerprint','password','private_key','passphrase','sudo_password','node_name','node_id','expected_revision','api_url','mode','gpus','python','roles','request_id'}
    if set(raw)-allowed: raise Rejected('unknown onboarding field')
    result={**{'port':22,'mode':'docker','gpus':'all','python':'python3','roles':['client'],'expected_revision':0},**raw}
    for field,limit in [('host',253),('username',64),('node_name',100),('fingerprint',80),('api_url',2048),('python',512),('gpus',256),('password',1024),('private_key',16384),('passphrase',1024),('sudo_password',1024),('node_id',100),('request_id',100)]:
        value=result.get(field,'')
        if not isinstance(value,str) or len(value)>limit or '\x00' in value: raise Rejected('invalid '+field)
    for field in ('host','username','node_name'):
        if not re.fullmatch(r'[A-Za-z0-9_.:-]+' if field=='host' else r'[A-Za-z0-9_.-]+',result.get(field,'')) or result[field].startswith('-'): raise Rejected('invalid '+field)
    if type(result['port']) is not int or not 1<=result['port']<=65535: raise Rejected('invalid SSH port')
    if type(result['expected_revision']) is not int or result['expected_revision']<0: raise Rejected('invalid management revision')
    if result['mode'] not in ('docker','host'): raise Rejected('invalid execution mode')
    if not isinstance(result['roles'],list) or not result['roles'] or any(x not in ('client','server') for x in result['roles']): raise Rejected('invalid roles')
    url=urlsplit(result.get('api_url',''))
    if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment or url.path.rstrip('/'):
        raise Rejected('API address must be an HTTP(S) origin reachable from the node')
    if not result['python'] or result['python'].startswith('-') or '\n' in result['python']: raise Rejected('invalid Python executable')
    if result['gpus']!='all':
        if not re.fullmatch(r'\d+(,\d+)*',result['gpus']): raise Rejected('invalid GPU selection')
        indices=[int(x) for x in result['gpus'].split(',')]
        if len(indices)>64 or len(set(indices))!=len(indices) or max(indices)>1023: raise Rejected('invalid GPU selection')
    if result.get('fingerprint') and not re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}',result['fingerprint']): raise Rejected('invalid host fingerprint')
    return result


def public_form(form):
    return {k:v for k,v in form.items() if k not in ('password','private_key','passphrase','sudo_password')}
