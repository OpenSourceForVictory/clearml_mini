"""Confirmed-install image preparation and bounded Docker resource/device probes."""
from pathlib import Path
import uuid
from .contracts import Rejected

DEFAULT_IMAGE='mini-eval-runner:dev'


def prepare(remote,python,root,image,kind,indices,stage):
    """Runs only after confirmation; the bundle at root is checksum validated."""
    if not isinstance(image,str) or not image or image.startswith('-'):
        raise Rejected('runner_image_invalid')
    stage('PREPARE_IMAGE')
    code,_,_=remote.run(['docker','image','inspect',image],check=False,timeout=20)
    if code:
        code,_,_=remote.run(['docker','pull','--quiet',image],check=False,timeout=180)
        if code:
            if image!=DEFAULT_IMAGE:raise Rejected('runner_image_pull_failed')
            code,_,_=remote.run(['docker','build','--quiet','-f',root+'/runner/Dockerfile','-t',image,root],check=False,timeout=900)
            if code:raise Rejected('runner_image_build_failed')
    stage('VERIFY_DOCKER')
    source=Path(__file__).resolve().parents[1].joinpath('worker/resource_limits.py').read_text()
    program="scope={'__name__':'limits'};exec("+repr(source)+",scope);scope['verify_current'](1,128*1024**2,32)"
    groups=indices if kind=='npu' else [None]
    for index in groups:
        name='mini-eval-onboarding-probe-'+uuid.uuid4().hex
        args=['docker','run','--rm','--name',name,'--network=none','--read-only','--cap-drop=ALL',
              '--security-opt=no-new-privileges','--cpus=1','--memory=128m','--pids-limit=32']
        command=program
        if kind=='npu':
            devices=[f'/dev/davinci{int(index)}','/dev/davinci_manager','/dev/devmm_svm','/dev/hisi_hdc']
            for device in devices:args+=['--device',device+':'+device+':rw']
            command+=";import os\nfor p in "+repr(devices)+":\n fd=os.open(p,os.O_RDWR|os.O_CLOEXEC);os.close(fd)"
        try:
            code,_,_=remote.run(args+['--entrypoint','python',image,'-c',command],check=False,timeout=40)
            if code:raise Rejected('docker_runtime_verification_failed')
        finally:
            remote.run(['docker','rm','-f',name],check=False,timeout=20)
