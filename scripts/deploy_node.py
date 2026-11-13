"""Transfer a trusted platform bundle over SSH, then run its complete installer."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import zipfile


def validate_bundle(payload, digest):
    if len(payload)>8*1024*1024 or hashlib.sha256(payload).hexdigest()!=digest:
        raise ValueError('bundle checksum or size mismatch')
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        names=set()
        if len(z.infolist())>1024 or sum(x.file_size for x in z.infolist())>24*1024*1024: raise ValueError('bundle too large')
        for x in z.infolist():
            p=PurePosixPath(x.filename)
            if p.is_absolute() or '..' in p.parts or str(p)!=x.filename or x.filename in names or (x.external_attr>>16)&0o170000==0o120000:
                raise ValueError('invalid bundle member')
            names.add(x.filename)
        if not {'scripts/install_node.py','scripts/install_worker_updater.py','worker/updater.py','runner/sdk.py','worker-release.zip','release.sha256'}<=names:
            raise ValueError('incomplete node bundle')


def deploy(bundle, credential, digest, target, port=22, identity=None, installer_args=(), sudo=False):
    payload=Path(bundle).read_bytes();validate_bundle(payload,digest)
    secret=Path(credential).read_bytes();json.loads(secret)
    if len(secret)>65536: raise ValueError('credential too large')
    if not re.fullmatch(r'[A-Za-z0-9_.@:\[\]-]+',target) or target.startswith('-'): raise ValueError('invalid SSH target')
    if not 1<=port<=65535: raise ValueError('invalid SSH port')
    base=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10','-p',str(port)]
    if identity: base+=['-i',str(identity)]
    base.append(target)
    def run(argv, **kwargs):
        return subprocess.run(base+[shlex.join(argv)],check=True,capture_output=True,timeout=300,**kwargs)
    remote=None
    print('Checking SSH host identity and preparing a private transfer directory.')
    try:
        remote=run(['mktemp','-d','/tmp/mini-eval-node.XXXXXXXX']).stdout.decode().strip()
        if not re.fullmatch(r'/tmp/mini-eval-node\.[A-Za-z0-9]+',remote): raise ValueError('invalid remote temporary directory')
        # SSH stdin carries credentials; command arguments and progress never contain them.
        receiver="import os,sys; p=sys.argv[1]; f=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); out=os.fdopen(f,'wb'); out.write(sys.stdin.buffer.read()); out.close()"
        print('Transferring complete node package and credentials over SSH.')
        run(['python3','-c',receiver,remote+'/bundle.zip'],input=payload)
        run(['python3','-c',receiver,remote+'/credential.json'],input=secret)
        extract="import hashlib,sys,zipfile; p=sys.argv[1]; data=open(p+'/bundle.zip','rb').read(); assert hashlib.sha256(data).hexdigest()==sys.argv[2]; zipfile.ZipFile(p+'/bundle.zip').extractall(p)"
        run(['python3','-c',extract,remote,digest])
        argv=['python3','-B',remote+'/scripts/install_node.py','--credential',remote+'/credential.json',*installer_args]
        if sudo: argv=['sudo','-n',*argv]
        print('Installing Worker, runner/SDK and independent updater; preserving original node settings.')
        run(argv)
        print('Complete node package transferred and installed. Check platform enrollment readiness.')
    finally:
        if remote:
            try: run(['rm','-rf','--',remote])
            except subprocess.SubprocessError: pass


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',type=Path,required=True);p.add_argument('--credential',type=Path,required=True)
    p.add_argument('--sha256',required=True);p.add_argument('--target',required=True)
    p.add_argument('--port',type=int,default=22);p.add_argument('--identity',type=Path);p.add_argument('--sudo',action='store_true')
    a,extra=p.parse_known_args()
    try: deploy(a.bundle,a.credential,a.sha256,a.target,a.port,a.identity,extra,a.sudo)
    except (ValueError,OSError,subprocess.SubprocessError):
        p.exit(1,'SSH deployment failed. Check known_hosts, key access, non-interactive sudo and node installation prerequisites; node remains unverified.\n')


if __name__=='__main__': main()
