"""Real GPU simulation check, without connecting to a model or counting eval results."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', choices=['adjust_bottle', 'beat_block_hammer'], default='adjust_bottle')
    parser.add_argument('--seed', type=int, default=100000)
    args = parser.parse_args()
    from .assets import ASSET_REVISION, validate
    validate(Path('/opt/mini-eval/assets/robotwin') / ASSET_REVISION)
    import numpy as np
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA is unavailable inside the container')
    from runner.robotwin_render import evaluate
    evaluate(1)
    root = Path(os.environ.get('ROBOTWIN_ROOT', '/opt/RoboTwin'))
    os.chdir(root)
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location('robotwin_helpers', root / 'scripts/eval_policy_xpolicylab.py')
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    config, _ = helpers.load_task_args({'task_name': args.task, 'task_config': 'demo_clean', 'policy_name': 'cosmos'})
    config.update(eval_mode=True, eval_video_log=False, eval_video_save_dir=None, render_freq=0)
    env = helpers.class_decorator(args.task)
    try:
        env.setup_demo(now_ep_num=0, seed=args.seed, is_test=True, **config)
        observation = env.get_obs()
        cameras = {}
        for name in ('head_camera', 'left_camera', 'right_camera'):
            rgb = np.asarray(observation['observation'][name]['rgb'])
            if rgb.ndim != 3 or rgb.shape[-1] != 3 or not np.isfinite(rgb).all() or rgb.std() <= 0:
                raise RuntimeError(f'invalid camera image: {name}: {rgb.shape}')
            cameras[name] = list(rgb.shape)
        print(json.dumps({'kind': 'environment-check', 'task': args.task, 'seed': args.seed,
                          'gpu': torch.cuda.get_device_name(), 'cameras': cameras,
                          'model_evaluation': False}), flush=True)
    finally:
        helpers.safe_close_env(env, clear_cache=True)


if __name__ == '__main__':
    main()
