"""Credential handling, host pinning, bounded IO and real local socket protocol."""
import io
import json
import socket
import threading
from types import SimpleNamespace as N

import pytest
from onboarding.contracts import Rejected
from onboarding import ssh


class Transport:
    def __init__(self,*args):self.closed=False;self.auth=None
    def start_client(self,**kwargs):pass
    def get_remote_server_key(self):return N(asbytes=lambda:b'test-key',get_name=lambda:'ssh-ed25519')
    def auth_publickey(self,user,key):self.auth=(user,key)
    def auth_password(self,user,password):self.auth=(user,password)
    def is_authenticated(self):return self.auth is not None
    def close(self):self.closed=True


@pytest.mark.parametrize('mode',['password','key','probe','wrong-key','bad-key','missing','authentication-error','connection-error','unauthenticated'])
def test_remote_open_pins_identity_and_sanitizes_failures(monkeypatch,mode):
    transport=Transport();key=transport.get_remote_server_key()
    form={'host':'test','port':22,'username':'worker','fingerprint':ssh.fingerprint(key),'password':'test-password'}
    monkeypatch.setattr(ssh.socket,'create_connection',lambda *a,**k:object())
    monkeypatch.setattr(ssh.paramiko,'Transport',lambda *a:transport)
    if mode in ('key','bad-key'):
        form['private_key']='test-only-key'
        def parse(*args,**kwargs):
            if mode=='bad-key':raise ssh.paramiko.SSHException('sensitive text')
            return 'parsed'
        for cls in (ssh.paramiko.Ed25519Key,ssh.paramiko.ECDSAKey,ssh.paramiko.RSAKey):monkeypatch.setattr(cls,'from_private_key',parse)
    if mode=='wrong-key':form['fingerprint']='SHA256:wrong'
    if mode=='missing':form.pop('password')
    if mode in ('authentication-error','connection-error'):
        def fail(*args):raise (ssh.paramiko.AuthenticationException if mode=='authentication-error' else OSError)('do not disclose')
        transport.auth_password=fail
    if mode=='unauthenticated':transport.is_authenticated=lambda:False
    remote=ssh.Remote(form)
    if mode=='probe':
        assert remote.open(False)=={'fingerprint':form['fingerprint'],'algorithm':'ssh-ed25519'};remote.close()
    elif mode in ('password','key'):
        with remote as opened:assert opened is remote and transport.auth[0]=='worker'
        assert transport.closed
    else:
        with pytest.raises(Rejected) as error:remote.open()
        assert 'sensitive' not in str(error.value) and 'disclose' not in str(error.value)
        assert transport.closed


class Channel:
    def __init__(self,out=b'ok',err=b'',code=0):self.out=out;self.err=err;self.code=code;self.closed=False;self.sent=b''
    def settimeout(self,value):self.timeout=value
    def exec_command(self,value):self.command=value
    def sendall(self,value):self.sent+=value
    def shutdown_write(self):pass
    def recv_ready(self):return bool(self.out)
    def recv_stderr_ready(self):return bool(self.err)
    def recv(self,size):v=self.out;self.out=b'';return v
    def recv_stderr(self,size):v=self.err;self.err=b'';return v
    def exit_status_ready(self):return True
    def recv_exit_status(self):return self.code
    def close(self):self.closed=True


@pytest.mark.parametrize('password',['','test-only'])
def test_ssh_command_quotes_arguments_and_sudo_input(password):
    channel=Channel();remote=ssh.Remote({'sudo_password':password});remote.transport=N(open_session=lambda **kw:channel)
    assert remote.run(['echo','space ; value'],data=b'payload',sudo=True)==(0,b'ok',b'')
    assert "'space ; value'" in channel.command
    assert channel.sent==(b'test-only\npayload' if password else b'payload')
    assert ('sudo -S' if password else 'sudo -n') in channel.command
    assert channel.closed


@pytest.mark.parametrize('failure',['size','timeout','exit'])
def test_ssh_bounds_output_timeout_and_exit(monkeypatch,failure):
    channel=Channel(out=b'x'*(1048577 if failure=='size' else 1),code=3 if failure=='exit' else 0)
    remote=ssh.Remote({});remote.transport=N(open_session=lambda **kw:channel)
    if failure=='timeout':
        channel.exit_status_ready=lambda:False
        clock=iter([0,100]);monkeypatch.setattr(ssh.time,'monotonic',lambda:next(clock))
    with pytest.raises(Rejected):remote.run(['command'],timeout=1)
    assert channel.closed
    if failure=='exit':
        assert remote.run(['command'],check=False)[0]==3


def test_upload_creates_private_exclusive_file_and_closes(monkeypatch):
    events=[]
    class File(io.BytesIO):
        def close(self):events.append(('content',self.getvalue()));super().close()
    sftp=N(get_channel=lambda:N(settimeout=lambda n:events.append(('timeout',n))),
           open=lambda path,mode:events.append(('open',path,mode)) or File(),
           chmod=lambda p,m:events.append(('chmod',p,m)),close=lambda:events.append(('closed',)))
    monkeypatch.setattr(ssh.paramiko.SFTPClient,'from_transport',lambda t:sftp)
    remote=ssh.Remote({});remote.upload('/tmp/new',b'payload')
    assert ('open','/tmp/new','wx') in events and ('chmod','/tmp/new',0o600) in events
    assert ('content',b'payload') in events and events[-1]==('closed',)


@pytest.fixture
def unix_executor(tmp_path):
    from onboarding.server import Server,Handler
    path=str(tmp_path/'executor.sock');server=Server(path,Handler)
    calls=[]
    server.operations=N(**{name:(lambda *args,_name=name: calls.append((_name,args)) or {'ok':True}) for name in ('list','start','confirm','trust','cancel')})
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request(route,body,length=None):
        raw=json.dumps(body).encode();connection=socket.socket(socket.AF_UNIX);connection.connect(path)
        connection.sendall(f'POST {route} HTTP/1.0\r\nContent-Length: {len(raw) if length is None else length}\r\n\r\n'.encode()+raw)
        with connection.makefile('rb') as response:content=response.read()
        connection.close();head,payload=content.split(b'\r\n\r\n',1)
        return int(head.split()[1]),json.loads(payload)
    yield request,calls
    server.shutdown();thread.join(2);server.server_close()


@pytest.mark.parametrize('route,name', [('/list','list'),('/check','start'),('/confirm','confirm'),('/trust','trust'),('/cancel','cancel')])
def test_local_executor_routes_authenticated_actor(unix_executor,route,name):
    request,calls=unix_executor
    code,value=request(route,{'actor':'operator','id':'operation','form':{},'authorization':'test-token','fingerprint':'SHA256:test'})
    assert code==200 and value=={'ok':True} and calls[0][0]==name


@pytest.mark.parametrize('route,body,length,code',[('/missing',{'actor':'a'},None,409),('/list',{'actor':''},None,409),('/list',{},None,400),('/list',{'actor':'a'},0,409),('/list',{'actor':'a'},65537,409)])
def test_local_executor_rejects_bad_requests(unix_executor,route,body,length,code):
    request,calls=unix_executor
    status,result=request(route,body,length)
    assert status==code and 'error' in result and not calls


def test_executor_main_private_socket_and_cleanup(tmp_path,monkeypatch):
    """Exercise socket ownership lifecycle without starting a daemon loop."""
    import sys
    import stat
    from onboarding import server
    path=tmp_path/'private'/'executor.sock';events=[]
    monkeypatch.setattr(sys,'argv',['executor','--socket',str(path),'--state',str(tmp_path/'state'),'--api-url','http://test'])
    class Operations:
        def __init__(self,root,api):
            assert root==tmp_path/'state' and api=='http://test'
        def expire(self):pass
        def close(self):events.append('operations-closed')
    real=server.Server
    class Bounded(real):
        def serve_forever(self):
            assert path.is_socket() and stat.S_IMODE(path.stat().st_mode)==0o600
            assert isinstance(self.operations,Operations)
            events.append('served')
    monkeypatch.setattr(server,'Operations',Operations)
    monkeypatch.setattr(server,'Server',Bounded)
    # Do not leave the daemon expiry thread running beyond the test.
    monkeypatch.setattr(server.threading,'Thread',lambda **kw:type('Thread',(),{'start':lambda self:None})())
    server.main()
    assert events==['served','operations-closed'] and not path.exists()
