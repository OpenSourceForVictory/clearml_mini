"""Checks real parameter/path propagation; this sample does not evaluate a model."""
import json
import os
from pathlib import Path


def load():
    config=json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
    assert os.environ['DEMO_LABEL']==config['business']['label']
    asset=Path(os.environ['DEMO_ASSETS'])/'manifest.json'
    assert json.loads(asset.read_text())['dataset']=='scheduling-demo-v1'
    work=Path(os.environ['MINI_EVAL_WORK_DIR'])
    output=Path(os.environ['MINI_EVAL_OUTPUT_DIR'])
    assert work!=output and work.is_dir() and output.is_dir()
    (work/'probe.txt').write_text(config['platform']['execution_id'])
    if os.environ.get('VERIFY_LIMITS')=='1':
        group=next(x[3:] for x in Path('/proc/self/cgroup').read_text().splitlines() if x.startswith('0::'))
        root=Path('/sys/fs/cgroup')/group.lstrip('/')
        quota,period=(root/'cpu.max').read_text().split()
        assert quota!='max' and int(quota)==int(period)
        assert int((root/'memory.max').read_text())==1024**3
        print('verified-resource-limits',flush=True)
    if os.environ.get('VERIFY_READ_ONLY')=='1':
        assert os.statvfs(asset.parent).f_flag & os.ST_RDONLY, 'asset mount lacks read-only flag'
        try:(asset.parent/'must-not-write').write_text('unexpected')
        except OSError:pass
        else:raise AssertionError('asset mount is writable')
    return config
