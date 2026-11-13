"""Fail closed before starting a Worker that depends on a shared data volume."""
import argparse
import json
from pathlib import Path
import subprocess


def check(path, source, filesystem, volume_id):
    root = Path(path)
    if not root.is_absolute() or root.is_symlink() or root.resolve() != root:
        raise ValueError('shared mount path must be absolute without symlinks')
    result = subprocess.run(['findmnt', '--json', '--mountpoint', str(root),
                             '--output', 'TARGET,SOURCE,FSTYPE,OPTIONS,UUID'],
                            capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise ValueError('shared volume is not mounted')
    mounts = json.loads(result.stdout).get('filesystems', [])
    if len(mounts) != 1:
        raise ValueError('expected exactly one shared mount')
    mount = mounts[0]
    source_matches = (mount.get('uuid') == source[5:] if source.startswith('UUID=')
                      else mount['source'] == source)
    if mount['target'] != str(root) or not source_matches or mount['fstype'] != filesystem:
        raise ValueError('shared volume source or filesystem does not match')
    if 'rw' not in mount.get('options', '').split(','):
        raise ValueError('shared volume is not writable')
    marker = root/'.mini-eval-volume-id'
    if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 128:
        raise ValueError('shared volume identity marker is missing or invalid')
    if marker.read_text().strip() != volume_id:
        raise ValueError('shared volume identity does not match')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--path', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--filesystem', required=True)
    parser.add_argument('--volume-id', required=True)
    args = parser.parse_args()
    try:
        check(args.path, args.source, args.filesystem, args.volume_id)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'Shared volume check failed: {exc}\n')


if __name__ == '__main__':
    main()
