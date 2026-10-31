"""Real Docker contract check, not a RoboTwin or model quality evaluation."""
import json
import os
from pathlib import Path
import time
from runner.sdk import EvaluationClient, EpisodeResult


class ImageClient(EvaluationClient):
    def run_episode(self, task, episode, context):
        asset = Path(os.environ['ASSETS'])
        assert str(asset) == '/data/robotwin'
        assert os.statvfs(asset).f_flag & os.ST_RDONLY
        assert os.environ['TASK'] == task['task_name']
        assert os.environ['LOG_LEVEL'] == 'acceptance'
        assert str(Path.cwd()) == '/workspace/eval'
        # The platform adapter was injected; it was not preinstalled in this image.
        import runner.sdk
        assert runner.sdk.__file__.startswith('/tmp/mini-eval-runtime/')
        marker = (asset/'marker.txt').read_text()
        (context.work_dir/'write-check.txt').write_text(marker)
        result={'marker':marker,'assets':str(asset),'work':str(context.work_dir),'output':str(context.output_dir),
                'task':task['task_name'],'episode':episode.id,'runtime':runner.sdk.__file__, 'log_level':os.environ['LOG_LEVEL']}
        name=f'mounts-{episode.id}.json'
        (context.output_dir/name).write_text(json.dumps(result))
        time.sleep(task['parameters'].get('sleep_seconds',1))
        return EpisodeResult(success=True,metrics={'runtime_check':1},artifacts=[name])


if __name__ == '__main__':
    from runner.sdk import run
    run(ImageClient(), json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text()))
