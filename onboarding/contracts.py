"""Bounded, secret-safe input contract shared by API and independent executor."""
import re
from urllib.parse import urlsplit

class Rejected(ValueError):
    pass


def validate(raw):
    if not isinstance(raw,dict): raise Rejected('invalid onboarding form')
    allowed={'host','port','username','fingerprint','password','private_key','passphrase','sudo_password','node_name','node_id','expected_revision','api_url','mode','gpus','python','roles','request_id','accelerator_type','business_uid','business_gid','business_user','workspace_root','npu_sharing','gpu_sharing'}
    if set(raw)-allowed: raise Rejected('unknown onboarding field')
    business_user=raw.get('business_user') or raw.get('username','')
    default_id=0 if business_user=='root' else 29000
    result={**{'port':22,'mode':'preserve' if raw.get('node_id') else 'docker','gpus':'all','python':'python3','roles':['client'],'expected_revision':0,'accelerator_type':'gpu','business_user':business_user,'business_uid':default_id,'business_gid':default_id,'workspace_root':'','npu_sharing':'preserve','gpu_sharing':'preserve'},**raw}
    for field,limit in [('host',253),('username',64),('business_user',64),('node_name',100),('fingerprint',80),('api_url',2048),('python',512),('gpus',256),('workspace_root',4096),('password',1024),('private_key',16384),('passphrase',1024),('sudo_password',1024),('node_id',100),('request_id',100)]:
        value=result.get(field,'')
        if not isinstance(value,str) or len(value)>limit or '\x00' in value: raise Rejected('invalid '+field)
    for field in ('host','username','business_user','node_name'):
        if not re.fullmatch(r'[A-Za-z0-9_.:-]+' if field=='host' else r'[A-Za-z0-9_.-]+',result.get(field,'')) or result[field].startswith('-'): raise Rejected('invalid '+field)
    if result['node_name'] in ('.','..'):
        raise Rejected('invalid node_name')
    if result['business_user']!=result['username'] and not result.get('node_id'):
        raise Rejected('business account must match SSH account')
    if type(result['port']) is not int or not 1<=result['port']<=65535: raise Rejected('invalid SSH port')
    if type(result['expected_revision']) is not int or result['expected_revision']<0: raise Rejected('invalid management revision')
    if result['mode'] not in ('docker','host','both','preserve') or (result['mode']=='preserve' and not result.get('node_id')): raise Rejected('invalid execution mode')
    if result['accelerator_type'] not in ('gpu','npu'):raise Rejected('invalid accelerator type')
    if result['npu_sharing'] not in ('preserve','enabled','disabled') or (result['accelerator_type']!='npu' and result['npu_sharing']!='preserve'):
        raise Rejected('invalid NPU sharing choice')
    if result['gpu_sharing'] not in ('preserve','enabled','disabled') or (result['accelerator_type']!='gpu' and result['gpu_sharing']!='preserve'):
        raise Rejected('invalid GPU sharing choice')
    for name in ('business_uid','business_gid'):
        if type(result[name]) is not int or not (0<=result[name]<=60000 and (result[name]==0) == (result.get('business_user')=='root')):raise Rejected('invalid business UID/GID')
    if result['workspace_root']:
        from pathlib import PurePosixPath
        path=PurePosixPath(result['workspace_root'])
        if not path.is_absolute() or str(path)=='/' or '..' in path.parts or ',' in result['workspace_root'] or any(ord(c)<32 or ord(c)==127 for c in result['workspace_root']):
            raise Rejected('invalid workspace root')
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
