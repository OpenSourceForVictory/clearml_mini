"""GPU environment check based on RoboTwin's scripts/test_render.py.

Reference: RoboTwin-Platform/RoboTwin@ea8b21121ebb3cd201ff5b3fe361944ac94eda3f.
This checks SAPIEN physics/rendering, not robot policy performance.
"""
import argparse
import json
import time


def evaluate(episodes):
    import numpy as np
    import sapien.core as sapien

    started = time.monotonic()
    engine = sapien.Engine()
    renderer = sapien.SapienRenderer()
    engine.set_renderer(renderer)
    scene = engine.create_scene(sapien.SceneConfig())
    scene.set_timestep(1 / 120)
    scene.add_ground(0)
    scene.set_ambient_light([0.5, 0.5, 0.5])
    scene.add_directional_light([0, 1, -1], [1, 1, 1])
    builder = scene.create_actor_builder()
    builder.add_box_collision(half_size=[0.1, 0.1, 0.1])
    builder.add_box_visual(half_size=[0.1, 0.1, 0.1], material=[1, 0, 0])
    actor = builder.build(name='render-check-box')
    body = actor.find_component_by_type(sapien.physx.PhysxRigidDynamicComponent)
    camera = scene.add_camera('check', width=64, height=64, fovy=1.0, near=0.1, far=10)
    camera.set_entity_pose(sapien.Pose([-1, 0, 0.3]))
    for index in range(episodes):
        actor.set_pose(sapien.Pose([0, 0, 0.5]))
        body.set_linear_velocity([0, 0, 0])
        body.set_angular_velocity([0, 0, 0])
        for _ in range(120):
            scene.step()
        scene.update_render()
        camera.take_picture()
        pixels = camera.get_picture('Color')
        if pixels.shape != (64, 64, 4) or not np.isfinite(pixels).all():
            raise RuntimeError('SAPIEN returned invalid image data')
        if float(pixels[:, :, :3].std()) < 0.001:
            raise RuntimeError('SAPIEN returned an empty or uniform image')
        height = float(actor.get_pose().p[2])
        if not 0.08 < height < 0.15:
            raise RuntimeError(f'Physics ground contact failed: box height={height}')
        print(json.dumps({'episode': index + 1, 'check': 'sapien-render-and-physics',
                          'success': True, 'image_std': float(pixels[:, :, :3].std()),
                          'box_height': height}), flush=True)
    return {'success_rate': 1.0, 'episode_count': episodes, 'runtime': time.monotonic() - started}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--model-ref', choices=['sapien-render'], required=True)
    parser.add_argument('--benchmark', choices=['robotwin-render'], required=True)
    parser.add_argument('--episodes', type=int, choices=range(1, 101), required=True)
    args = parser.parse_args()
    print(json.dumps({'benchmark': args.benchmark, 'kind': 'environment-check',
                      'upstream_commit': 'ea8b21121ebb3cd201ff5b3fe361944ac94eda3f',
                      'sapien_version': '3.0.0b1'}), flush=True)
    print(json.dumps({'final': evaluate(args.episodes)}), flush=True)


if __name__ == '__main__':
    main()
