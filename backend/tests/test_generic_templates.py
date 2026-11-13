from backend.tests.test_execution import env

def definition(mode):
    return {'name':'sample-'+mode,'version':'1','command':['python3','business.py'],'execution_mode':mode,
            **({'image':'client:1'} if mode=='docker' else {'host_runtime':{'environment_name':'simulation'}})}


def test_two_kinds_and_modes_and_versions(env):
    _,c,a=env
    for kind in ['client','server']:
        for mode in ['docker','host']:
            path='/api/v1/admin/'+kind+'-templates';body=definition(mode)
            r=c.post(path,headers=a['admin'],json=body);assert r.status_code==201,r.text
            value=r.json();assert value['execution_mode']==mode
            if mode=='host':assert value['image']=='' and value['host_runtime']['environment_name']=='simulation'
            assert c.post(path,headers=a['admin'],json=body).status_code==409
            copied=c.post(path+'/'+value['id']+'/clone',headers=a['admin'],json={'version':'2'})
            assert copied.status_code==201,copied.text
            assert copied.json()['command']==body['command']
            assert c.delete(path+'/'+value['id'],headers=a['admin']).status_code==200
            assert c.patch(path+'/'+value['id'],headers=a['admin'],json={'enabled':True}).status_code==404


def test_mode_validation_and_authority(env):
    _,c,a=env
    host=definition('host')
    assert c.post('/api/v1/client-templates',headers=a['alice'],json=host).status_code==403
    assert c.post('/api/v1/admin/client-templates',headers=a['admin'],json={**host,'image':'bad:1'}).status_code==400
    assert c.post('/api/v1/admin/server-templates',headers=a['alice'],json=definition('docker')).status_code==403
    assert c.post('/api/v1/admin/client-templates',headers=a['admin'],json={**host,'host_runtime':None}).status_code==400
    r=c.post('/api/v1/admin/client-templates',headers=a['admin'],json={**host,'integration_mode':'sdk-command'})
    assert r.status_code==201 and r.json()['episode_reporting']
