"""Download official RoboTwin assets on the worker host; publish only a verified version."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import zipfile

ASSET_REVISION = '3dc3b798668feb99ac61cc9086d84cbcc3d79186'
ASSETS = {
    'embodiments.zip': '85ffeff55a5066def5931224a85cfa3f8abaa1fbf779bd17789a7fb3f85bc789',
    'objects.zip': '6aa56b3cf1e1064f7c809308144da36b00815f8b137fef2d7e4de856f8becf27',
    'background_texture.zip': '54ede0fb5b783e0faa2bc98720d3affd6ca3bb9280b225b48c1aafaf31473070',
}


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def extract_verified(archive, destination, expected):
    if digest(archive) != expected:
        raise RuntimeError(f'asset checksum mismatch: {archive.name}')
    with zipfile.ZipFile(archive) as z:
        for entry in z.infolist():
            if not (destination / entry.filename).resolve().is_relative_to(destination.resolve()):
                raise RuntimeError('asset archive contains unsafe path')
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise RuntimeError('asset archive contains symlink')
        z.extractall(destination)


def validate(root):
    manifest = root / 'manifest.json'
    if not manifest.is_file():
        raise RuntimeError(f'RoboTwin assets are not ready: {manifest}; complete the host asset download first')
    record = json.loads(manifest.read_text())
    if record != {'revision': ASSET_REVISION, 'sha256': ASSETS}:
        raise RuntimeError('RoboTwin asset version/manifest does not match the client image')
    for name in ('embodiments', 'objects', 'background_texture'):
        if not (root / name).is_dir():
            raise RuntimeError(f'RoboTwin asset directory is missing: {name}')


def install(assets_root, cache, base_url):
    parent = assets_root / 'robotwin'
    parent.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    target = parent / ASSET_REVISION
    with (parent / '.download.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if target.exists():
            validate(target)
            print(f'Assets already ready: {target}', flush=True)
            return
        staging = parent / ('.' + ASSET_REVISION + '.partial')
        staging.mkdir(exist_ok=True)
        for filename, checksum in ASSETS.items():
            archive = cache / filename
            if not archive.exists():
                partial = cache / (filename + '.part')
                print(f'Downloading {filename} (resume supported)', flush=True)
                subprocess.run(['curl', '--fail', '--location', '--retry', '8',
                                '--connect-timeout', '30', '--continue-at', '-',
                                '--output', str(partial), f"{base_url.rstrip('/')}/{filename}"], check=True)
                if digest(partial) != checksum:
                    raise RuntimeError(f'asset checksum mismatch: {partial}; retain for inspection, remove before retry')
                partial.rename(archive)
            print(f'Verifying and extracting {filename}', flush=True)
            extract_verified(archive, staging, checksum)
        # Official templates need container paths, not paths on this particular host.
        templates = list((staging / 'embodiments').rglob('*_tmp.yml'))
        if not templates:
            raise RuntimeError('official embodiment config templates are missing')
        for template in templates:
            content = template.read_text().replace('${ASSETS_PATH}', '/opt/RoboTwin').replace('$ASSETS_PATH', '/opt/RoboTwin')
            template.with_name(template.name.replace('_tmp.yml', '.yml')).write_text(content)
        (staging / 'manifest.json').write_text(json.dumps({'revision': ASSET_REVISION, 'sha256': ASSETS}, indent=2))
        validate(staging)
        staging.rename(target)
        print(f'Assets ready: {target}', flush=True)


def verify_published(root, cache):
    """Explicit delivery audit, never a scheduler or client startup prerequisite."""
    validate(root)
    files = 0
    generated = 0
    for filename, checksum in ASSETS.items():
        archive = cache / filename
        if digest(archive) != checksum:
            raise RuntimeError(f'asset checksum mismatch: {filename}')
        with zipfile.ZipFile(archive) as z:
            for entry in z.infolist():
                path = root / entry.filename
                if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                    raise RuntimeError(f'unsafe published asset path: {entry.filename}')
                if entry.is_dir():
                    if not path.is_dir():
                        raise RuntimeError(f'published asset directory is missing: {entry.filename}')
                    continue
                if not path.is_file() or path.stat().st_size != entry.file_size:
                    raise RuntimeError(f'published asset missing or wrong size: {entry.filename}')
                expected = hashlib.sha256()
                with z.open(entry) as source:
                    for block in iter(lambda: source.read(8 * 1024 * 1024), b''):
                        expected.update(block)
                if digest(path) != expected.hexdigest():
                    raise RuntimeError(f'published asset content mismatch: {entry.filename}')
                files += 1
                if entry.filename.startswith('embodiments/') and path.name.endswith('_tmp.yml'):
                    config = path.with_name(path.name.replace('_tmp.yml', '.yml'))
                    content = path.read_text().replace('${ASSETS_PATH}', '/opt/RoboTwin').replace('$ASSETS_PATH', '/opt/RoboTwin')
                    if config.is_symlink() or not config.is_file() or config.read_text() != content:
                        raise RuntimeError(f'generated embodiment config mismatch: {config.name}')
                    generated += 1
    if not generated:
        raise RuntimeError('official embodiment config templates are missing')
    return {'revision': ASSET_REVISION, 'archives': len(ASSETS), 'files_verified': files,
            'generated_configs_verified': generated, 'model_evaluation': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--assets-root', required=True, type=Path)
    p.add_argument('--cache-dir', required=True, type=Path)
    p.add_argument('--base-url', default=f'https://huggingface.co/datasets/TianxingChen/RoboTwin2.0/resolve/{ASSET_REVISION}')
    p.add_argument('--verify-only', action='store_true', help='Read-only audit of published files against the pinned cached archives')
    a = p.parse_args()
    if a.verify_only:
        print(json.dumps(verify_published(a.assets_root.resolve() / 'robotwin' / ASSET_REVISION, a.cache_dir.resolve())))
        return
    install(a.assets_root.resolve(), a.cache_dir.resolve(), a.base_url)


if __name__ == '__main__':
    main()
