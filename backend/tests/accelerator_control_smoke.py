"""Opt-in isolated GPU/NPU short program through the recovery runtime stop path.

No platform claim, scheduling, or model evaluation is performed. Run only on an
idle device; the unique transient unit is stopped and removed on every exit.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace
import uuid

from worker.recovery_runtime import RecoveryRuntime


def run(accelerator, python, device, cann_set_env=None, user_unit=False):
    execution = str(uuid.uuid4())
    unit = 'mini-eval-exec-' + execution + '.service'
    root = Path(tempfile.mkdtemp(prefix='mini-eval-accelerator-control-'))
    ready = root / 'ready.json'
    program = root / 'business.py'
    program.write_text('''import json,os,time
from pathlib import Path
import torch
if os.environ["SMOKE_ACCELERATOR"] == "npu":
    import torch_npu
    torch.npu.set_device(0)
    value = torch.ones(16, device="npu:0")
    device = "npu"
else:
    value = torch.ones(16, device="cuda:0")
    device = "cuda"
assert float((value * 2).sum().cpu()) == 32.0
Path(os.environ["SMOKE_READY"]).write_text(json.dumps({"device":device,"result":32.0}))
time.sleep(60)
''')
    worker = SimpleNamespace(path=root / 'journal.json', workspace=root,
        state={'task': {'execution_id': execution}},
        config={'host_execution': not user_unit}, host_mode=lambda: True)
    runtime = RecoveryRuntime(worker, execution)
    environment = ['--setenv=SMOKE_READY=' + str(ready),
                   '--setenv=SMOKE_ACCELERATOR=' + accelerator]
    environment.append('--setenv=' + ('CUDA_VISIBLE_DEVICES' if accelerator == 'gpu'
                                    else 'ASCEND_RT_VISIBLE_DEVICES') + '=' + str(device))
    if accelerator == 'npu':
        if not cann_set_env or not Path(cann_set_env).is_file():
            raise ValueError('a readable CANN set_env.sh is required for NPU smoke')
        command = ['/bin/bash', '-c', 'source "$1" && exec "$2" "$3"',
                   'mini-eval-control-smoke', cann_set_env, python, str(program)]
    else:
        command = [python, str(program)]
    launched = False
    try:
        subprocess.run(['systemd-run', *(['--user'] if user_unit else []),
                        '--quiet', '--unit=' + unit,
                        '--property=Description=Mini Eval execution ' + execution,
                        '--property=RemainAfterExit=yes', '--property=TasksMax=infinity',
                        '--working-directory=' + str(root), *environment, *command], check=True)
        launched = True
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            current = runtime.inspect()
            if ready.exists() and current['state'] == 'running':
                break
            if current['state'] != 'running':
                raise RuntimeError('accelerator program exited before its ready marker')
            time.sleep(.2)
        else:
            raise TimeoutError('accelerator program did not reach ready marker')
        result = json.loads(ready.read_text())
        bound = current['identity']
        worker.state['recovery'] = {'generation': 'isolated-accelerator-smoke',
                                    'identity': bound}
        runtime.stop(bound)
        stopped = runtime.inspect()
        if stopped['state'] != 'exited' or stopped['identity'] != bound:
            raise RuntimeError('original accelerator runtime was not stopped')
        return {'accelerator': accelerator, 'device': str(device),
                'execution_id': execution, 'runtime_id': bound['runtime_id'],
                'calculation': result, 'stopped': True}
    finally:
        if launched:
            subprocess.run(['systemctl', *(['--user'] if user_unit else []), 'stop', unit],
                           check=False, capture_output=True)
            subprocess.run(['systemctl', *(['--user'] if user_unit else []), 'reset-failed', unit],
                           check=False, capture_output=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--accelerator', choices=['gpu', 'npu'], required=True)
    parser.add_argument('--python', required=True)
    parser.add_argument('--device', required=True)
    parser.add_argument('--cann-set-env')
    parser.add_argument('--user-unit', action='store_true',
                        help='use the current user systemd manager')
    args = parser.parse_args()
    print(json.dumps(run(args.accelerator, args.python, args.device,
                         args.cann_set_env, args.user_unit), sort_keys=True))


if __name__ == '__main__':
    main()
