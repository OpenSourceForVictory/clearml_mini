"""Opt-in two-card, same-name service/client runtime isolation smoke (not a model)."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace
import uuid

from worker.recovery_runtime import RecoveryRuntime


def run(python, cann, devices):
    root = Path(tempfile.mkdtemp(prefix='mini-eval-multicard-'))
    program = root / 'business.py'
    program.write_text('''import json,os,time
from pathlib import Path
import torch,torch_npu
torch.npu.set_device(0)
v=torch.ones(16,device='npu:0')
assert float((v*2).sum().cpu())==32.0
Path(os.environ['SMOKE_READY']).write_text(json.dumps({'value':32.0,'visible':os.environ['ASCEND_RT_VISIBLE_DEVICES']}))
time.sleep(180)
''')
    entries = []
    try:
        for index, role in enumerate(('service', 'service', 'client', 'client')):
            execution = str(uuid.uuid4())
            task = {'execution_id': execution, 'service_name': 'same-service-name',
                    'service_instance_id': str(uuid.uuid4()) if role == 'service' else None}
            unit = 'mini-eval-exec-' + execution + '.service'
            ready = root / (execution + '.json')
            worker = SimpleNamespace(path=root / (execution + '.journal'), workspace=root,
                state={'task': task}, config={'host_execution': True}, host_mode=lambda: True)
            runtime = RecoveryRuntime(worker, execution)
            entries.append((unit, ready, runtime, role, devices[index % 2]))
            subprocess.run(['systemd-run', '--quiet', '--unit=' + unit,
                '--property=Description=Mini Eval execution ' + execution,
                '--property=RemainAfterExit=yes', '--property=TasksMax=infinity',
                '--setenv=ASCEND_RT_VISIBLE_DEVICES=' + devices[index % 2],
                '--setenv=SMOKE_READY=' + str(ready), '/bin/bash', '-c',
                'source "$1" && exec "$2" "$3"', 'multicard-smoke', cann, python, str(program)], check=True)
        deadline = time.monotonic() + 120
        while not all(e[1].exists() for e in entries):
            if time.monotonic() > deadline:
                raise TimeoutError('NPU programs did not become ready')
            time.sleep(.5)
        observed = [e[2].inspect() for e in entries]
        assert all(o['state'] == 'running' for o in observed)
        assert len({o['identity']['runtime_id'] for o in observed}) == 4
        for entry, state in zip(entries, observed):
            entry[2].worker.state['recovery'] = {'generation': 'isolated-smoke', 'identity': state['identity']}
        entries[1][2].stop(observed[1]['identity'])
        assert entries[1][2].inspect()['state'] == 'exited'
        assert all(entries[i][2].inspect()['state'] == 'running' for i in (0, 2, 3))
        return {'same_name': True, 'independent_stop': True, 'executions': [
            {'role': e[3], 'device': e[4], 'result': json.loads(e[1].read_text()),
             'execution_id': e[2].execution_id, 'identity': o['identity']}
            for e, o in zip(entries, observed)]}
    finally:
        for unit, _, _, _, _ in reversed(entries):
            subprocess.run(['systemctl', 'stop', unit], capture_output=True, check=False)
            subprocess.run(['systemctl', 'reset-failed', unit], capture_output=True, check=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', required=True)
    parser.add_argument('--cann', required=True)
    parser.add_argument('--devices', nargs=2, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.python, args.cann, args.devices), sort_keys=True))
