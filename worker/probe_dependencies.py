"""Fixed probe dependencies, installed outside immutable releases and credentials."""
import importlib
import os
from pathlib import Path
import subprocess
import sys
import fcntl

PACKAGES=('aiohttp==3.14.5','aiodns==3.6.1','async-timeout==5.0.1')


def ready():
    try:
        from importlib.metadata import version,PackageNotFoundError
        return all(version(name)==wanted for name,wanted in (item.split('==') for item in PACKAGES))
    except PackageNotFoundError:return False


def ensure():
    root=Path(__file__).resolve().parents[2]/('.mini-eval-probes-'+str(sys.version_info.major)+'.'+str(sys.version_info.minor))
    sys.path.insert(0,str(root))
    if ready():return
    with root.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if ready():return
        # Fixed arguments, no template-supplied package names or index URLs.
        subprocess.run([*pip_command(root.parent),'install','--disable-pip-version-check','--target',str(root),'--upgrade',*PACKAGES],
                       check=True,timeout=120,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        importlib.invalidate_caches()
        if not ready():raise RuntimeError('probe dependencies unavailable; prepare fixed packages before enrollment')


def pip_command(directory):
    """Use a pinned PyPA zip application when system Python has no pip."""
    import importlib.util
    import hashlib
    import tempfile
    import urllib.request
    if importlib.util.find_spec('pip') is not None:return [sys.executable,'-m','pip']
    path=Path(directory)/'.mini-eval-pip-25.2.pyz'
    digest='769a10214aca718618e7014ec8e70b36288fe9d49a9d7ba3185ea3917e84a4d3'
    def verified():
        return path.is_file() and not path.is_symlink() and path.stat().st_size<=2000000 and hashlib.sha256(path.read_bytes()).hexdigest()==digest
    if not verified():
        with urllib.request.urlopen('https://bootstrap.pypa.io/pip/zipapp/pip-25.2.pyz',timeout=15) as response:data=response.read(2000001)
        if len(data)>2000000 or hashlib.sha256(data).hexdigest()!=digest:raise RuntimeError('pip bootstrap checksum mismatch')
        with tempfile.NamedTemporaryFile(dir=directory,delete=False) as temporary:
            temporary.write(data);temporary.flush();os.fsync(temporary.fileno());name=temporary.name
        os.replace(name,path)
    return [sys.executable,str(path)]
