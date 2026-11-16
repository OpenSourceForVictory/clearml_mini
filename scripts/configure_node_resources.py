"""Administrator-only, idempotent cgroup delegation; never restarts a node or user session."""
import argparse
import os
from pathlib import Path
import pwd
import subprocess


def configure(uid, root=Path('/etc/systemd/system')):
    if os.geteuid() != 0:
        raise PermissionError('administrator permission required')
    account = pwd.getpwuid(uid)
    if uid <= 0 or account.pw_shell.endswith(('nologin', '/false')):
        raise ValueError('choose a regular Worker service user')
    if not Path('/sys/fs/cgroup/cgroup.controllers').is_file():
        raise ValueError('cgroup v2 requires planned host maintenance; no boot configuration changed')
    directory = root/f'user@{uid}.service.d'
    target = directory/'60-mini-eval-resources.conf'
    content = '[Service]\n# Mini Eval: delegate resource controllers to the Worker user.\nDelegate=cpu memory pids\n'
    if directory.is_symlink() or target.is_symlink():
        raise ValueError('refusing symlink configuration')
    if target.exists() and target.read_text() != content:
        raise ValueError('existing managed drop-in differs; review before replacing it')
    directory.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with target.open('x') as stream: stream.write(content)
        target.chmod(0o644)
    subprocess.run(['systemctl', 'daemon-reload'], check=True, timeout=30)
    subprocess.run(['loginctl', 'enable-linger', account.pw_name], check=True, timeout=30)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uid', type=int, required=True)
    args = parser.parse_args()
    print(configure(args.uid))
