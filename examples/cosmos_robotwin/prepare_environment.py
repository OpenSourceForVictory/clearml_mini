"""Apply the two dependency patches from the pinned RoboTwin installer."""
import importlib.util
from pathlib import Path


def main():
    # Match the pinned official installer without silently patching unknown versions.
    patches = [
        ('sapien', 'wrapper/urdf_loader.py', 'open(urdf_file, "r")', 'open(urdf_file, "r", encoding="utf-8")'),
        ('sapien', 'wrapper/urdf_loader.py', 'open(srdf_file, "r")', 'open(srdf_file, "r", encoding="utf-8")'),
        ('mplib', 'planner.py', 'if np.linalg.norm(delta_twist) < 1e-4 or collide or not within_joint_limit:',
         'if np.linalg.norm(delta_twist) < 1e-4 or not within_joint_limit:'),
    ]
    for package, relative, old, new in patches:
        path = Path(importlib.util.find_spec(package).origin).parent / relative
        content = path.read_text()
        if old not in content:
            raise RuntimeError(f'upstream patch target changed: {path}')
        path.write_text(content.replace(old, new))


if __name__ == '__main__':
    main()
