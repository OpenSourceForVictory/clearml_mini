"""Run on a dedicated Docker build host, never inside the API service.

Processes one build per invocation. ADMIN_API_TOKEN is read only by this process;
it is not passed to Docker build arguments, environment, or source context.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request


def api(base, path, body):
    request = urllib.request.Request(base.rstrip('/')+'/api/v1/'+path,
        data=json.dumps(body).encode(), headers={'Content-Type': 'application/json',
        'Authorization': 'Bearer '+os.environ['ADMIN_API_TOKEN']})
    with urllib.request.urlopen(request, timeout=30) as r:
        return json.load(r)


def build_source(build, base_image, image, push=False):
    # Revalidate sources even when the server is trusted; never interpolate a user Dockerfile.
    from backend.app.builds import SourceSubmit
    source = SourceSubmit(name=build['name'], version=build['version'], **build['source'])
    for ref in (base_image, image):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:@-]*', ref):
            raise ValueError('invalid configured image reference')
    with tempfile.TemporaryDirectory(prefix='eval-build-') as tmp:
        root = Path(tmp)
        for name, content in source.files.items():
            target = root/'client'/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        dockerfile = ('FROM '+base_image+'\nWORKDIR /app/client\nCOPY client/ /app/client/\n'
            'ENV PYTHONPATH=/app:/app/client\n'
            'RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi\n'
            'RUN python -m compileall -q /app/client\n'
            'CMD ["python", "-m", "runner.cs_entry"]\n')
        module, class_name = source.entrypoint.split(':', 1)
        check = ('import importlib; from runner.sdk import EvaluationClient; '
                 'client=getattr(importlib.import_module('+repr(module)+'),'+repr(class_name)+'); '
                 'assert issubclass(client, EvaluationClient), "client must inherit EvaluationClient"')
        dockerfile += 'RUN '+json.dumps(['python', '-c', check])+'\n'
        (root/'Dockerfile').write_text(dockerfile)
        with tempfile.TemporaryFile() as log:
            try:
                # Timeout is shorter than the server's lease. The host is explicitly dedicated.
                process = subprocess.run(['docker', 'build', '--force-rm', '-t', image, str(root)],
                                         stdout=log, stderr=subprocess.STDOUT, timeout=1500)
                if process.returncode == 0 and push:
                    process = subprocess.run(['docker', 'push', image], stdout=log, stderr=subprocess.STDOUT, timeout=120)
                status = 'SUCCEEDED' if process.returncode == 0 else 'FAILED'
            except subprocess.TimeoutExpired:
                log.write(b'\nBuild deadline exceeded.\n'); status = 'FAILED'
            log.seek(0, 2); size = log.tell(); log.seek(max(0, size-60000))
            output = log.read().decode(errors='replace')
        return {'token': build['token'], 'status': status, 'image': image if status == 'SUCCEEDED' else None, 'log': output}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api', required=True)
    parser.add_argument('--base-image', default='mini-eval-client-base:1')
    parser.add_argument('--image-prefix', default='mini-eval-user')
    parser.add_argument('--push', action='store_true')
    args = parser.parse_args()
    build = api(args.api, 'admin/client-builds/claim', {})['build']
    if build is None:
        print('No pending client builds'); return
    try:
        result = build_source(build, args.base_image, args.image_prefix+':'+build['id'], args.push)
    except Exception as exc:
        result = {'token': build['token'], 'status': 'FAILED', 'image': None, 'log': str(exc)[-60000:]}
    api(args.api, 'admin/client-builds/'+build['id']+'/complete', result)
    print(build['id'], result['status'])


if __name__ == '__main__':
    main()
