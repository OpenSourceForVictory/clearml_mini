"""Pinned host identity, memory-only credentials, bounded SSH IO."""
import base64
import hashlib
import io
import json
import shlex
import socket
import time
import paramiko
from .contracts import Rejected


def fingerprint(key):return 'SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')


class Remote:
    def __init__(self,form):self.form=form;self.transport=None
    def open(self,authenticate=True):
        f=self.form
        try:
            sock=socket.create_connection((f['host'],f['port']),timeout=10)
            self.transport=paramiko.Transport(sock);self.transport.banner_timeout=10;self.transport.auth_timeout=15
            self.transport.start_client(timeout=15)
            key=self.transport.get_remote_server_key();actual=fingerprint(key)
            if not authenticate:return {'fingerprint':actual,'algorithm':key.get_name()}
            if not f.get('fingerprint') or actual!=f['fingerprint']:raise Rejected('host_key_changed')
            if f.get('private_key'):
                parsed=None
                for cls in (paramiko.Ed25519Key,paramiko.ECDSAKey,paramiko.RSAKey):
                    try:parsed=cls.from_private_key(io.StringIO(f['private_key']),password=f.get('passphrase') or None);break
                    except (paramiko.SSHException,ValueError):continue
                if parsed is None:raise Rejected('invalid_private_key')
                self.transport.auth_publickey(f['username'],parsed)
            elif f.get('password'):
                self.transport.auth_password(f['username'],f['password'])
            else:raise Rejected('ssh_credential_required')
            if not self.transport.is_authenticated():raise Rejected('ssh_authentication_failed')
            return self
        except paramiko.AuthenticationException: self.close();raise Rejected('ssh_authentication_failed') from None
        except Rejected:self.close();raise
        except Exception:self.close();raise Rejected('ssh_connection_failed') from None
    def __enter__(self):return self.open()
    def __exit__(self,*a):self.close()
    def close(self):
        if self.transport:self.transport.close()
    def run(self,argv,data=b'',timeout=40,sudo=False,check=True):
        if sudo:
            password=self.form.get('sudo_password','')
            argv=(['sudo','-S','-p',''] if password else ['sudo','-n'])+argv
            if password:data=password.encode()+b'\n'+data
        channel=self.transport.open_session(timeout=10);channel.settimeout(10)
        try:
            channel.exec_command(shlex.join(argv))
            if data:channel.sendall(data)
            channel.shutdown_write()
            start=time.monotonic();out=bytearray();err=bytearray()
            while True:
                for target,ready,read in ((out,channel.recv_ready,channel.recv),(err,channel.recv_stderr_ready,channel.recv_stderr)):
                    if ready():target.extend(read(65536))
                if len(out)+len(err)>1024*1024:raise Rejected('remote_output_limit')
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():break
                if time.monotonic()-start>timeout:raise Rejected('remote_command_timeout')
                time.sleep(.02)
            code=channel.recv_exit_status()
            if check and code:raise Rejected('remote_command_failed')
            return code,bytes(out),bytes(err)
        finally:channel.close()
    def upload(self,path,data):
        sftp=paramiko.SFTPClient.from_transport(self.transport)
        try:
            sftp.get_channel().settimeout(20)
            with sftp.open(path,'wx') as f:
                sftp.chmod(path,0o600);f.write(data)
        finally:sftp.close()
