"""Check client prerequisites without a model endpoint or importing RoboTwin."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from .client import UPSTREAM_COMMIT, asset_path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--environment-only', action='store_true')
    p.add_argument('--assets-root', type=Path, default=Path('/opt/mini-eval/assets'))
    p.add_argument('--eef-config', default='cosmos/eef.json')
    a=p.parse_args()
    cosmos=Path(os.environ.get('COSMOS_ROOT','/opt/cosmos-framework'))
    robotwin=Path(os.environ.get('ROBOTWIN_ROOT','/opt/RoboTwin'))
    for name in ('numpy','msgpack','cv2','websockets.sync.client','torch','sapien','mplib','curobo','open3d','pytorch3d'):
        if importlib.util.find_spec(name) is None: raise RuntimeError('missing client dependency: '+name)
    for name in ('numpy', 'cv2', 'torch', 'sapien', 'mplib', 'open3d', 'pytorch3d.ops',
                 'curobo.curobolib.kinematics_fused_cu', 'curobo.curobolib.geom_cu'):
        importlib.import_module(name)
    for path in (cosmos/'examples/action_pretrain/robotwin_client.py',robotwin/'scripts/eval_policy_xpolicylab.py'):
        if not path.is_file(): raise FileNotFoundError(str(path))
    commit=subprocess.check_output(['git','-C',str(cosmos),'rev-parse','HEAD'],text=True).strip()
    if commit!=UPSTREAM_COMMIT: raise RuntimeError('unexpected Cosmos version: '+commit)
    encoders=subprocess.check_output(['ffmpeg','-hide_banner','-encoders'],stderr=subprocess.DEVNULL,text=True)
    if 'libx264' not in encoders: raise RuntimeError('ffmpeg libx264 encoder is required')
    if not a.environment_only:
        from .assets import ASSET_REVISION, validate
        validate(a.assets_root / 'robotwin' / ASSET_REVISION)
        config=json.loads(asset_path(a.assets_root,a.eef_config).read_text())
        import sys
        sys.path.insert(0,str(cosmos))
        from cosmos_framework.inference.causal_action.robotwin_client import RobotwinPoseConverter
        RobotwinPoseConverter(**config)
    print('Cosmos client dependencies and paths verified; simulator assets/GPU execution still require runtime validation.')


if __name__=='__main__': main()
