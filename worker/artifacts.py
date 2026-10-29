"""Read bounded regular files from docker cp without extracting host paths."""
import subprocess
import tarfile
import threading
from runner.cs_contract import OUTPUT_DIR, MAX_ARTIFACT_BYTES, MAX_ARTIFACT_COUNT, MAX_ARTIFACT_TOTAL, artifact_name


def read_artifacts(container):
    process = subprocess.Popen(['docker', 'cp', f'{container}:{OUTPUT_DIR}', '-'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    timer = threading.Timer(20, process.kill)
    timer.start()
    files, total, scanned = [], 0, 0
    try:
        try:
            with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
                for member in archive:
                    scanned += 1
                    if scanned > 128:
                        raise RuntimeError('artifact archive contains too many entries')
                    prefix, _, name = member.name.partition('/')
                    if prefix != OUTPUT_DIR.rsplit('/', 1)[-1]:
                        raise RuntimeError('invalid artifact archive root')
                    if member.isdir():
                        if name.rstrip('/') and not artifact_name(name.rstrip('/')):
                            raise RuntimeError('invalid artifact directory')
                        continue
                    if not member.isfile() or not artifact_name(name):
                        raise RuntimeError('artifacts must be regular files with safe relative names')
                    if len(files) >= MAX_ARTIFACT_COUNT or member.size > MAX_ARTIFACT_BYTES or total+member.size > MAX_ARTIFACT_TOTAL:
                        raise RuntimeError('artifact quota exceeded: 8 files, 8 MiB/file, 32 MiB/run')
                    if any(n == name for n, _ in files):
                        raise RuntimeError('duplicate artifact name')
                    data = archive.extractfile(member).read(MAX_ARTIFACT_BYTES+1)
                    if len(data) != member.size:
                        raise RuntimeError('truncated artifact')
                    total += len(data)
                    files.append((name, data))
        except tarfile.ReadError:
            error = process.stderr.read(4096).decode(errors='replace')
            if 'Could not find the file' in error or 'no such file or directory' in error.lower():
                return []
            raise RuntimeError('could not read client artifacts')
        if process.wait(timeout=5) != 0:
            raise RuntimeError('artifact copy failed or timed out')
        return files
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close(); process.stderr.close()
