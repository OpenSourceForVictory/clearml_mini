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
        subprocess.run([sys.executable,'-m','pip','install','--disable-pip-version-check','--target',str(root),'--upgrade',*PACKAGES],
                       check=True,timeout=120,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        importlib.invalidate_caches()
        if not ready():raise RuntimeError('probe dependencies unavailable; prepare fixed packages before enrollment')
