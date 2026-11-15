"""Run only in a disposable mount namespace; never mounts a host business path."""
import json
from pathlib import Path
import subprocess
import tempfile
import time
from node_agent.state import State
from node_agent.monitor import Monitor
from worker.workspace import validate_storage

with tempfile.TemporaryDirectory() as temp:
    root=Path(temp);share=root/'share';share.mkdir()
    subprocess.run(['mount','-t','tmpfs','tmpfs',str(share)],check=True)
    mounted=True
    state=State(root/'local-control')
    try:
        for name in ('work','assets','cache'):(share/name).mkdir()
        config={'workspace_root':str(share/'work'),'assets_root':str(share/'assets'),
                'cache_root':str(share/'cache'),'min_free_disk_gb':0}
        validate_storage(config,root/'worker-state.json')
        subprocess.run(['umount',str(share)],check=True);mounted=False
        try:validate_storage(config,root/'worker-state.json')
        except ValueError:pass
        else:raise AssertionError('missing workspace accepted')
        assert not (share/'work').exists(),'must not silently recreate a lost mount workspace'
        state.event('started')
        assert json.loads(state.path.read_text())['next_event']==2
        monitor=Monitor(state.root)
        for _ in range(30):
            sample=monitor.sample(5)
            if sample.get('disk',{}).get('status')=='ok':break
            time.sleep(.1)
        assert sample['disk']['status']=='ok'
        print(json.dumps({'missing_workspace':'rejected','local_agent_state':'durable','local_disk_probe':'ok'}))
    finally:
        state.close()
        if mounted:subprocess.run(['umount',str(share)],check=True)
